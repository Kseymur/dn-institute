#!/bin/env python

import argparse
import os
import sys
import json
from github import Github

# Local imports
from tools.git import get_pull_request, get_diff_by_url, parse_diff
from tools.utils import logging_decorator
from tools.content_parsers import extract_json, remove_plus
import tools.claude_retriever
from tools.claude_retriever.searcher.searchtools.websearch import BraveSearchTool


def parse_cli_args():
    """
    Parse CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--github-token", dest="github_token", help="GitHub token", required=True
    )
    parser.add_argument(
        "--api-key", dest="API_key", help="API key", required=True
    )
    parser.add_argument(
        "--pull-url", dest="pull_url", help="GitHub pull URL", required=True
    )
    parser.add_argument(
        "--search-api-key", dest="SEARCH_API_KEY", help="API key for the search engine", required=True
    )
    return parser.parse_args()


def api_call(query, client, model):
    """
    Make an API call and return the response.
    """
    try:
        return client.completion_with_retrieval(
            query=query,
            model=model,
            n_search_results_to_use=1,
            max_searches_to_try=3,
            max_tokens_to_sample=2000
        )
    except Exception as e:
        print(f"Error in API call: {e}")
        return None


@logging_decorator("Comment on PR")
def create_comment_on_pr(pull_request, answer):
    """
    Create and post a comment on a Github pull request.
    """
    try:
        comment = generate_comment(answer)
        print(comment)
        # only post comment if running on Github Actions
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pull_request.create_issue_comment(comment)
    except Exception as e:
        print(f"Error creating a comment on PR: {e}")


def generate_comment(answer):
    """
    Generate a formatted comment based on the provided answer.
    """
    comment = "## Fact-Checking Results\n\n"
    for claim in answer["fact-checking"]:
        emoji = ":white_check_mark:" if claim["result"].lower() == "true" else ":x:"
        comment += f"- **Claim**: {claim['claim']} {emoji}\n"
        comment += f"  - **Source**: [{claim['source']}]({claim['source']})\n"
        if claim["result"].lower() == "false":
            comment += f"  - **Explanation**: {claim['explanation']}\n"
        comment += "\n"

    comment += "## Spell-Checking Results\n\n"
    for mistake in answer["spell-checking"]:
        comment += f"- **Mistake**: `{mistake['mistake']}`\n"
        comment += f"  - **Correction**: `{mistake['correction']}`\n"
        comment += f"  - **Context**: `{mistake['context']}`\n"
        comment += "\n"

    emoji_hugo = ":white_check_mark:" if answer['hugo-checking'].lower() == "true" else ":x:"
    comment += f"## Hugo SSG Formatting Check\n- Does it match Hugo SSG formatting? {emoji_hugo}\n\n"

    emoji_filename = ":white_check_mark:" if answer['submission_guidelines']['is_filename_correct'].lower() == "true" else ":x:"
    comment += f"## Filename Check\n- Correct Filename: `{answer['submission_guidelines']['correct_filename']}`\n"
    comment += f"- Your Filename: `{answer['submission_guidelines']['article_filename']}` {emoji_filename}\n\n"

    emoji_sections = ":white_check_mark:" if answer['submission_guidelines']['has_allowed_headers'].lower() == "true" else ":x:"
    comment += f"## Section Headers Check\n- Allowed Headers: `{', '.join(answer['submission_guidelines']['allowed_headers'])}`\n"
    comment += f"- Your Headers: `{', '.join(answer['submission_guidelines']['headers_from_text'])}` {emoji_sections}\n\n"

    emoji_headers = ":white_check_mark:" if answer['submission_guidelines']['has_allowed_metadata_headers'].lower() == "true" else ":x:"
    comment += f"## Metadata Headers Check\n- Allowed Metadata Headers: `{', '.join(answer['submission_guidelines']['allowed_metadata_headers'])}`\n"
    comment += f"- Your Metadata Headers: `{', '.join(answer['submission_guidelines']['metadata_headers_from_text'])}` {emoji_headers}\n"

    return comment


PROMPT = """Conduct a comprehensive review of the provided text by performing both fact-checking and spell-checking. 
For fact-checking, identify each factual claim and verify its accuracy against reliable web sources. For each claim, cross-reference specific details such as numbers, dates, monetary values, and named entities with information from credible websites.
For spell-checking, identify and correct any spelling, grammatical, and punctuation mistakes.
Pay attention to the fact that the text is a Markdown document with headers for Hugo SSG. Check if it conforms to the requirements of this format.
Also, the text should comply with the submission guidelines. Extract a filename from the text and check if it matches the desirable format "YYYY-MM-DD-entity-that-was-hacked.md".
Then extract all headers from the text and check if they are included in the set of allowed headers: ("## Summary", "## Attackers", "## Losses", "## Timeline", "## Security Failure Causes"). If there are any extra or missing headers, return False.
Then extract all metadata headers located between "---" and "---" from the text and check if they are included in the set of allowed metadata headers: ("date", "target-entities", "entity-types", "attack-types", "title", "loss"). If there are any extra or missing metadata headers, return False.

Present your findings only in a structured JSON format.
Output Format: 
{
  "fact-checking": [
    {
      "claim": "[Exact factual statement from the text]",
      "source": "[Direct URL or the name of the credible source where the verification information was found]",
      "result": "[True or False]",
      "explanation": "[why it is False; if result is True the field should be empty]"
    }
  ],
  "spell-checking": [
  {"context": "[the sentence with a mistake]",
   "mistake": "[the mistake]",     
   "correction": "[the correction of the mistake]"    
   }  
   ],
   "hugo-checking": "[True or False]",
   "submission_guidelines": {
        "article_filename": "[an extracted filename with .md]", 
        "correct_filename": "[the corrected filename]",
        "is_filename_correct": "[True or False]",
        "allowed_headers": "[the list of allowed headers]",    
        "headers_from_text": "[a list of headers from the text]",    
        "has_allowed_headers": "[True or False]",
        "allowed_metadata_headers": "[the list of allowed metadata headers]",
        "metadata_headers_from_text": "[a list of metadata headers from the text]",
        "has_allowed_metadata_headers": "[True or False]" 
        }
        
  }
Example:
Input Text: "bla-bla.md: In July 2011, BTC-e, a cryptocurrency exchange, experienced a security breach that resulted in the loss of around 4,500 BTC."

Output: {"fact-checking": 
    [
    {"claim": "In July 2011, BTC-e experienced a security breach.",
    "source": "https://bitcoinmagazine.com/business/btc-e-attacked-1343738085",
    "result": "False",
    "explanation": "BTC-e experienced a security breach in July 2012, not 2011"}
    ],
    "spell-checking": [
    {"context": "a cryptocurrency exchange",
    "mistake": "exchange",     
    "correction": "exchange"    
    }  
    ],
    "hugo-checking": "False",
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

Text for Verification: ```%s```
"""


def main():
    args = parse_cli_args()
    with open('config.json', 'r') as config_file:
        config = json.load(config_file)

    search_tool = BraveSearchTool(brave_api_key=args.SEARCH_API_KEY, summarize_with_claude=True,
                                  anthropic_api_key=args.API_key)
    model = config['ANTHROPIC_SEARCH_MODEL']
    client = claude_retriever.ClientWithRetrieval(api_key=args.API_key, search_tool=search_tool)

    github = Github(args.github_token)
    pr = get_pull_request(github, args.pull_url)
    _diff = get_diff_by_url(pr)
    diff = parse_diff(_diff)

    print('-' * 50)
    print(diff)
    print('-' * 50)


    text = remove_plus(diff[0]['header'] + diff[0]['body'][0]['body'])

    print('-' * 50)
    print(text)
    print('-' * 50)

    query = PROMPT % text

    answer = api_call(query, client, model)
    print('-' * 50)
    print(answer)
    print('-' * 50)

    extracted_answer = extract_json(answer)
    print('-' * 50)
    print(extracted_answer)
    print('-' * 50)
    create_comment_on_pr(pr, extracted_answer)


# if __name__ == '__main__':
#     main()