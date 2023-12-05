#!/bin/env python

"""
Searches for similar texts on the wiki and checks whether a text from a pull request contains new information
"""

import argparse
import os
import sys
import json
import re
from github import Github
import openai

from tools.content_parsers import remove_plus
from tools.git import get_pull_request, get_diff_by_url, parse_diff


def parse_cli_args():
    """
    Parse CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--github-token", dest="github_token", help="GitHub token", required=True
    )
    parser.add_argument(
        "--llm-api-key", dest="API_key", help="API key", required=True
    )
    parser.add_argument(
        "--pull-url", dest="pull_url", help="GitHub pull URL", required=True
    )
    return parser.parse_args()


def openai_call(prompt: str, config, retry: int = None):
    """
    Make an API call and return the response.
    """
    model = config["GPT_MODEL"]
    temperature = config["GPT_temperature"]
    max_tokens = config["GPT_max_tokens"]
    if retry is None:
        retry = config["GPT_retry"]

    messages = [{"role": "user", "content": prompt}]
    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            n=1,
            stop=None,
        )
    except Exception as ex:
        if retry == 0:
            raise ex
        print(ex)
        print(f"Retry - {retry}, waiting 15 seconds")
        time.sleep(15)
        return openai_call(prompt, config, retry - 1)

    ret = response.choices[0].message.content.strip()
    return ret


def new_text_handler(diff):
    """
    Extracts text and target entity from a new pull request.
    """
    new_text = remove_plus(diff[0]['header'] + diff[0]['body'][0]['body'])
    pattern = r'target-entities:\s+(.*?)\n'
    matches = re.search(pattern, new_text)
    target = ''
    if matches:
        target_entities = matches.group(1)
        target = target_entities
    else:
        print("Value 'target-entities' didn't find")
    return new_text, target


    PROMPT = """Compare two texts and say if they are the same.
First: ```%s```

Second: ```%s```

If the texts say the same thing, return True.  Output should be machine-readable, for example:
```{
  "have_same_article": true|false
}```"""


def main():
    args = parse_cli_args()
    with open('tools/config.json', 'r') as config_file:
        config = json.load(config_file)

    github = Github(args.github_token)
    pr = get_pull_request(github, args.pull_url)
    _diff = get_diff_by_url(pr)
    diff = parse_diff(_diff)
    new_text, target = new_text_handler(diff)
    print('-' * 50)
    print(new_text)
    print('-' * 50)
    print('-' * 50)
    print(target)
    print('-' * 50)


if __name__ == '__main__':
    main()