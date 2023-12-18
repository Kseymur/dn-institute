from typing import Optional, Tuple
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT
from .searcher.types import SearchTool, SearchResult, Tool
import logging
import re
from utils import format_results_full
import json

logger = logging.getLogger(__name__)

EXTRACTING_PROMPT = """
Please extract important statements that appear to be factual from the text provided between <text></text> tags.
Return the extracted statements as a list. Skip the preamble; go straight into the result.
Also, return the number of extracted statements between tags <number_of_statements></number_of_statements>.
Aim to extract only important statements with numbers, dates, and names of organizations. There should not be too many extracted statements.

<text>{text}</text>
"""

RETRIEVAL_PROMPT = """
You are given a list of factual statements. Your job is to verify the accuracy of each statement using the search engine tool. Here is the description of the search engine tool: <tool_description>{description}</tool_description>

For each statement, create a query to verify its accuracy and insert it within <search_query> tags like so: <search_query>query</search_query>. 
You will then receive results within <search_result></search_result> tags. Use these results to determine the accuracy of each statement, providing a result of 'True', 'False' or 'Unverified'. 
Place the result between tags <result></result>. Also, put the Web Page URL between tags <source></source>. 
If there is no URL, put 'None' in the <source></source> tags.
 If the result is False, provide an explanation why between tags <explanation></explanation>.
Specifically, check the accuracy of numbers, dates, monetary values, and names of people or entities. 

If a query already in <search_query>query</search_query> tags, don't try to verify it. 
Also, place each statement itself between tags: <statement></statement>.

Here are the statements: {statements}
"""

ANSWER_PROMPT = """
Please review and verify the provided text.

Fact-Checking: Using the information provided within the <fact_checking_results></fact_checking_results> tags, please form the output  with results of fact-checking. There should be required fields "statement", "source", "result". If the result is False, provide an explanation why. If there is no source, put "None" in the "source" field.

Spell-Checking: Scan the text between <text></text> for any spelling, grammatical, and punctuation mistakes. List each mistake you find, providing the incorrect and corrected versions.

Additionally, since the text between <text></text> is a Markdown document for Hugo SSG, ensure it adheres to specific formatting requirements:

Check if the text between <text></text> follows the Markdown format, including appropriate headers.
Confirm if it meets submission guidelines, particularly the file naming convention ("YYYY-MM-DD-entity-that-was-hacked.md"). Extract the name of the file from the text and compare it to the correct name.
Verify that the document includes only the allowed headers: "## Summary", "## Attackers", "## Losses", "## Timeline", "## Security Failure Causes".
Check for the presence of specific metadata headers between "---" lines, such as "date", "target-entities", "entity-types", "attack-types", "title", "loss". The document must contain all and only allowed metadata headers.
Present your findings only in a valid, machine-readable JSON format. Skip the preamble; go straight into the JSON result.
Example:
Input Text: "bla-bla.md: In July 2011, BTC-e, a cryptocurrency exchange, experienced a security breach that resulted in the loss of around 4,500 BTC."
Output example: {"fact_checking": 
    [
    {"statement": "In July 2011, BTC-e experienced a security breach.",
    "source": "https://bitcoinmagazine.com/business/btc-e-attacked-1343738085",
    "result": "False",
    "explanation": "BTC-e experienced a security breach in July 2012, not 2011"
    }
    ],
    "spell_checking": [
    {"context": "a cryptocurrency exchange",
    "mistake": "exchange",     
    "correction": "exchange"    
    }  
    ],
    "hugo_checking": "False",
    "submission_guidelines": {
        "article_filename": "bla-bla.md", 
        "correct_filename": "2012-07-16-BTC-e.md",
        "is_filename_correct": "False",
        "allowed_headers": ["## Summary", "## Attackers", "## Losses", "## Timeline", "## Security Failure Causes"],    
        "headers_from_text": "None",    
        "has_allowed_headers": "False",
        "allowed_metadata_headers": ["date", "target-entities", "entity-types", "attack-types", "title", "loss"],
        "metadata_headers_from_text": "None",
        "has_allowed_metadata_headers": "False" 
        }
}
<fact_checking_results>%s</fact_checking_results> 
<text>%s</text>
"""

class ClientWithRetrieval(Anthropic):

    def __init__(self, search_tool: Optional[SearchTool] = None, verbose: bool = True, *args, **kwargs):
        """
        Initializes the ClientWithRetrieval class.
        
        Parameters:
            search_tool (SearchTool): SearchTool object to handle searching
            verbose (bool): Whether to print verbose logging
            *args, **kwargs: Passed to superclass init
        """
        super().__init__(*args, **kwargs)
        self.search_tool = search_tool
        self.verbose = verbose
    
    def extract_statements(self, text: str, model: str, temperature: float = 0.0, max_tokens_to_sample: int = 1000):
        prompt = f"{HUMAN_PROMPT} {EXTRACTING_PROMPT}<text>{text}</text>{AI_PROMPT}"
        completion = self.completions.create(prompt=prompt, model=model, temperature=temperature, max_tokens_to_sample=max_tokens_to_sample).completion
            
        return completion
    

    def retrieve(self,
                       query: str,
                       model: str,
                       n_search_results_to_use: int = 3,
                       stop_sequences: list[str] = [HUMAN_PROMPT],
                       max_tokens_to_sample: int = 1000,
                       max_searches_to_try: int = 5,
                       temperature: float = 1.0) -> str:
        """
        Main method to retrieve relevant search results for a query with a provided search tool.
        
        Constructs RETRIEVAL prompt with query and search tool description. 
        Keeps sampling Claude completions until stop sequence hit.
        
        Returns string with fact-checking results
        """
        assert self.search_tool is not None, "SearchTool must be provided to use .retrieve()"

        description = self.search_tool.tool_description
        statements = self.extract_statements(query, model=model, max_tokens_to_sample=max_tokens_to_sample, temperature=temperature)
        print("Statements:", statements)
        num_of_statements = int(self.extract_between_tags("number_of_statements", statements, strip=True))
        print("num_of_statements:", num_of_statements)
        prompt = f"{HUMAN_PROMPT} {RETRIEVAL_PROMPT.format(statements=statements, description=description)}{AI_PROMPT}"
        print("Prompt:", prompt)
        token_budget = max_tokens_to_sample
        all_raw_search_results: list[SearchResult] = []
        completions = ""
        for tries in range(num_of_statements):
            partial_completion = self.completions.create(prompt = prompt,
                                                     stop_sequences=stop_sequences + ['</search_query>'],
                                                     model=model,
                                                     max_tokens_to_sample = token_budget,
                                                     temperature = temperature)
            print("Partial completion:", partial_completion.completion)
            completions += partial_completion.completion
            partial_completion, stop_reason, stop_seq = partial_completion.completion, partial_completion.stop_reason, partial_completion.stop # type: ignore
            logger.info(partial_completion)
            token_budget -= self.count_tokens(partial_completion)
            prompt += partial_completion
            if stop_reason == 'stop_sequence' and stop_seq == '</search_query>':
                logger.info(f'Attempting search number {tries}.')
                raw_search_results, formatted_search_results = self._search_query_stop(partial_completion, n_search_results_to_use)
                prompt += '</search_query>' + formatted_search_results
                completions += '</search_query>' + formatted_search_results
                all_raw_search_results += raw_search_results
            else:
                break
        print("all_completions:", completions)
        return completions
    

    def answer_with_results(self, search_results: str, query: str, model: str, temperature: float):
        """Generates an RAG response based on search results and a query. If format_results is True,
           formats the raw search results first. Set format_results to True if you are using this method standalone without retrieve().

        Returns:
            str: Claude's answer to the query
        """
        
        try:
            prompt = f"{HUMAN_PROMPT} {ANSWER_PROMPT % (search_results, query)}{AI_PROMPT}"
        except Exception as e:
            print(str(e))
        
        print("Prompt:", prompt)
        
        try:
            answer = self.completions.create(
                prompt=prompt, 
                model=model, 
                temperature=temperature, 
                max_tokens_to_sample=3000
            ).completion
        except Exception as e:
            answer = str(e)
        
        return answer
    

    def completion_with_retrieval(self,
                                        query: str,
                                        model: str,
                                        n_search_results_to_use: int = 3,
                                        stop_sequences: list[str] = [HUMAN_PROMPT],
                                        max_tokens_to_sample: int = 1000,
                                        max_searches_to_try: int = 5,
                                        temperature: float = 1.0) -> str:
        """
        Gets a final completion from retrieval results        
        
        Calls retrieve() to get search results.
        Calls answer_with_results() with search results and query.
        
        Returns:
            str: Claude's answer to the query
        """
        search_results = self.retrieve(query, model=model,
                                                 n_search_results_to_use=n_search_results_to_use, stop_sequences=stop_sequences,
                                                 max_tokens_to_sample=max_tokens_to_sample,
                                                 max_searches_to_try=max_searches_to_try,
                                                 temperature=temperature)
        print("Search results:", search_results)
        answer = self.answer_with_results(search_results, query, model, temperature)
        return answer
    

    # Helper methods
    def _search_query_stop(self, partial_completion: str, n_search_results_to_use: int) -> Tuple[list[SearchResult], str]:
        """
        Helper to handle search query stop case.
        
        Extracts search query from completion text.
        Runs search using SearchTool. 
        Formats search results.
        
        Returns:
            tuple: 
                list[SearchResult]: Raw search results
                str: Formatted search result text
        """
        assert self.search_tool is not None, "SearchTool was not provided for client"

        search_query = self.extract_between_tags('search_query', partial_completion + '</search_query>') 
        if search_query is None:
            raise Exception(f'Completion with retrieval failed as partial completion returned mismatched <search_query> tags.')
        if self.verbose:
            logger.info('\n'+'-'*20 + f'\nPausing stream because Claude has issued a query in <search_query> tags: <search_query>{search_query}</search_query>\n' + '-'*20)
        logger.info(f'Running search query against SearchTool: {search_query}')
        search_results = self.search_tool.raw_search(search_query, n_search_results_to_use)
        extracted_search_results = self.search_tool.process_raw_search_results(search_results)
        formatted_search_results = format_results_full(extracted_search_results)

        if self.verbose:
            logger.info('\n' + '-'*20 + f'\nThe SearchTool has returned the following search results:\n\n{formatted_search_results}\n\n' + '-'*20 + '\n')
        return search_results, formatted_search_results
    

    def extract_between_tags(self, tag, string, strip=True):
        """
        Helper to extract text between XML tags.
        
        Finds last match of specified tags in string.
        Handles edge cases and stripping.
        
        Returns:
            str: Extracted string between tags
        """
        ext_list = re.findall(f"<{tag}\\s?>(.+?)</{tag}\\s?>", string, re.DOTALL)
        if strip:
            ext_list = [e.strip() for e in ext_list]
        
        if ext_list:
            return ext_list[-1]
        else:
            return None