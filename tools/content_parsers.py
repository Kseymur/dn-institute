import re
import json


def extract_json(text):
    json_pattern = r'```json\s*(\{.*?\})\s*```'
    match = re.search(json_pattern, text, re.DOTALL)

    if match:
        json_text = match.group(1)
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            return "Invalid JSON format"
    else:
        return "JSON not found"


def remove_plus(text):
    return '\n'.join(line.lstrip('+') for line in text.split('\n'))