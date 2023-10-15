#!/bin/env python

"""
Searches for similar texts on the wiki and checks whether a text from a pull request contains new information
"""

import os, sys, argparse, time
from typing import List, Tuple
import json
from tools.utils import logging_decorator
from tools.git import get_pull_request, get_diff_by_url
from github import Github
import openai
import tiktoken
import requests
from bs4 import BeautifulSoup
import re

# parse arguments
parser = argparse.ArgumentParser()
parser.add_argument(
    "--github-token", dest="github_token", help="GitHub token", required=True
)
parser.add_argument(
    "--openai-key", dest="openai_key", help="OpenAI API key", required=True
)
parser.add_argument(
    "--pull-url", dest="pull_url", help="GitHub pull URL", required=True
)

parser.add_argument("--content-path", dest="content_path", help="Content path")
parser.add_argument("--mode", dest="mode", help="Run mode")
args = parser.parse_args()

openai.api_key = args.openai_key

token_usage = {"prompt": 0, "completion": 0}

config = {
    "model": "gpt-3.5-turbo",
    "retry": 3,
    "temperature": 0.5,
    "max_tokens": 500,
    "search_size": 10,
}

if args.mode == "development":
    config["retry"] = 1
    config["search_size"] = 1


def count_tokens(text):
    encoding = tiktoken.encoding_for_model("gpt-3.5-turbo-0301")
    return len(encoding.encode(text))


def openai_call(
        prompt: str,
        model: str = config["model"],
        retry: int = config["retry"],
        temperature: float = config["temperature"],
        max_tokens: int = config["max_tokens"],
):
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
            raise (ex)
        print(ex)
        print(f"Retry - {retry}, waiting 15 seconds")
        time.sleep(15)
        return openai_call(prompt, model, retry - 1)

    ret = response.choices[0].message.content.strip()
    token_usage["prompt"] += count_tokens(prompt)
    token_usage["completion"] += count_tokens(ret)
    return ret


def split_content(diff: str) -> List[Tuple[List[str], str]]:
    files = diff.split("diff --git")
    results = []
    for f in files:
        if f == "":
            continue
        # remove all tech info and stay only changed and append
        parts = f.split("\n")
        parts = [x for x in parts if x.startswith("+")]
        # remove file names
        parts = [x for x in parts if not x.startswith("+++")]
        # remove '+' sign from start
        parts = [x.lstrip("+") for x in parts]

        # Join string between # symbol
        final = []
        buff = []
        for p in parts:
            if p.startswith("#"):
                final.append("\n".join(buff))
                buff = []
            buff.append(p)
        final.append("\n".join(buff))

        if "---" in final[0] and "date:" in final[0] and "title:" in final[0]:
            meta = final[0].split("\n")
            meta = [
                x.replace("date: ", "")
                .replace("title: ", "")
                .removeprefix('"')
                .removesuffix('"')
                for x in meta
                if "date: " in x or "title: " in x
            ]
            meta_str = " ".join(meta)
        else:
            meta_str = ""
        final = [
            x for x in final if not x.startswith("---") and not x.endswith("---\n")
        ]
        results.append((final, meta_str))
    return results


CHECK_STATEMENT = """Compare two texts and say if they are the same.
First: ```%s```

Second: ```%s```

If the texts say the same thing, return True.  Output should be machine-readable, for example:
```{
  "have_same_article": true|false
}```"""


def new_text_handler(diff):
    # extracts text and target entity from a new pull request
    new_text = split_content(diff)
    pattern = r'target-entities:\s+(.*?)\n'
    matches = re.search(pattern, diff)
    target = ''
    if matches:
        target_entities = matches.group(1)
        target = target_entities
    else:
        print("Value 'target-entities' didn't find")
    return new_text, target


def get_list_of_target_entities(url):
    # gets a list of target entities that exist on the crypto wiki
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
    # searching urls of same texts in the crypto wiki
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


@logging_decorator("Check old texts")
def check_old_texts(href_list, url, new_text):
    # compares a new text with old texts
    have_same_article = False
    for href in href_list:
        url = url + href
        response = requests.get(url)
        if response.status_code == 200:
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            text = soup.text
            exact_text = re.search(r'Summary\n#.*', text, re.DOTALL)
            if exact_text:
                old_text = exact_text.group(0)
                ans = openai_call(CHECK_STATEMENT % (old_text, new_text))
                ans = ans.strip().strip("`").strip()
                ans = ans[ans.find("{"):]
                obj = json.loads(ans)
                if obj["have_same_article"]:
                    have_same_article = True
            else:
                return "Text after 'Summary' didn't find"
    return f"`" + "Is this article new for our wiki?" + "` " + ":x:" if have_same_article else ":white_check_mark:" + "\n\n"


def main():
    # initialize GitHub object
    github = Github(args.github_token)

    is_github_env = True if os.environ.get("GITHUB_ACTIONS") == "true" else False

    list_of_target_entities = get_list_of_target_entities('https://dn.institute/attacks/posts/target-entities/')

    if list_of_target_entities:
        pr = get_pull_request(github, args.pull_url)
        diff = get_diff_by_url(pr)

        if len(diff.strip()) < 1:
            print("No diff - exit")
            pass
        else:
            new_text, target = new_text_handler(diff)
            href_list = get_same_texts(target, 'https://dn.institute/attacks/posts/target-entities/', list_of_target_entities)

            if href_list:
                comment = check_old_texts(href_list, 'https://dn.institute', new_text)
            else:
                comment = f"`" + "Is this article new for our wiki?" + "` " + ":white_check_mark:" + "\n\n"

            if len(comment) > 0 and is_github_env:
                pr.create_issue_comment(comment)
                print("comment", comment)

    print("token_usage", token_usage)
