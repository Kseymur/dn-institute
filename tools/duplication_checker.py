#!/bin/env python

"""
Searches for similar texts on the wiki and checks whether a text from a pull request contains new information
"""

import argparse
import os
import sys
import time
import json
import re
from github import Github
import openai
from bs4 import BeautifulSoup
import requests

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
    Extracts text and target entity from a new pull request
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


def get_list_of_target_entities(url):
    """
    Gets a list of target entities that exist on the crypto wiki
    """
    result_list = []
    response = requests.get(url)
    if response.status_code == 200:
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        li_elements = soup.find_all('li', class_='section-item')
        for li in li_elements:
            a_element = li.find('a')
            if a_element:
                result_list.append(a_element.text)
    else:
        print("Failed to retrieve page content")
    return result_list
    

def get_same_texts(target, url, list_of_target_entities):
    """
    Searching urls of same texts in the crypto wiki
    """
    href_list = []
    if target in list_of_target_entities:
        url = url + target
        response = requests.get(url)
        if response.status_code == 200:
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            posts = soup.find_all('article', class_='markdown book-post')
            if posts:
                for post in posts:
                    post_head = post.find('h2')
                    href = post_head.find('a')
                    href = href['href']
                    href_list.append(href)
    return href_list


def check_old_texts(href_list, url, new_text, prompt, config):
    """
    Compares a new text from a pull request with old texts
    """
    have_same_article = False
    for href in href_list:
        url = url + href
        response = requests.get(url)
        if response.status_code == 200:
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            old_text = soup.get_text()
            old_text = re.search(r'Summary\n#.*', old_text, re.DOTALL)
            old_text = old_text.group(0)
            print(old_text)
            # ans = openai_call(prompt%(new_text, old_text), config)
    return "LAla"


PROMPT = """Compare two texts and say if they are the same.
First: ```%s```

Second: ```%s```

If the texts say the same thing, return True.  Output should be machine-readable, for example:
```{
  "have_same_article": true|false
}```"""


def main():
    args = parse_cli_args()
    openai.api_key = args.API_key
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
    list_of_target_entities = get_list_of_target_entities('https://dn.institute/attacks/posts/target-entities/')
    print('-' * 50)
    print(list_of_target_entities)
    print('-' * 50)
    href_list = get_same_texts(target, 'https://dn.institute/attacks/posts/target-entities/', list_of_target_entities)
    print('-' * 50)
    print(href_list)    
    print('-' * 50)
    check_old_texts(href_list, 'https://dn.institute', new_text, PROMPT, config)


if __name__ == '__main__':
    main()