import json
import time
import os
from openai import OpenAI, RateLimitError, APIError

reverse_engineered = []
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

def call_openai(prompt: str, retries: int = 3) -> str:
    system_message = "You are an expert systems engineer specialized in converting behavioral models into mission descriptions. I will provide you with a behavior tree that describes a real-world robot's behavior. Do keep your answer as simple text with a mix of imperative and descriptive language. Format your response as plain-text paragraphs. Strictly use the dictionary provided, as a reference to interpret behavior control of the corresponding node types. Avoid redundant ending statements. Use the example given to guide your sentence fluency."

    for attempt in range(retries):
        try:
            response = client.responses.create(
                model="gpt-5.6-luna",
                reasoning={"effort": "none"},
                input=[
                    {"role": "developer", "content": system_message},
                    {"role": "user", "content": prompt}
                ]
            )
            
            # Find the output message, skipping the reasoning item
            for block in response.output:
                if block.type == "message":
                    return block.content[0].text
        
        except RateLimitError:
            wait = 2 ** attempt  # exponential backoff: 1s, 2s, 4s
            print(f"Rate limited. Retrying in {wait}s...")
            time.sleep(wait)

        except APIError as e:
            print(f"API error: {e}")
            break

        except Exception as e:
            print(f"API error (attempt {attempt + 1}): {type(e).__name__}: {e}")
            break

    # Never return None: a silent None would be written to the dataset as
    # "mrq": null and corrupt the run without failing it.
    raise RuntimeError("call_openai failed to produce a response")


def build_prompt(entry: str)-> str:
    pdata = ""
    chat_prompt_data = 'prompt.txt'

    script_dir = os.path.dirname(os.path.abspath(__file__))
    chat_prompt_file_path = os.path.join(script_dir, chat_prompt_data)

    if not os.path.exists(chat_prompt_file_path):
        raise FileNotFoundError(f"File not found: {chat_prompt_file_path}")
    
    if os.path.getsize(chat_prompt_file_path) == 0:
        raise ValueError(f"File is empty: {chat_prompt_file_path}")
    
    with open(chat_prompt_file_path, 'r') as file:
        pdata = file.read()

    pdata = pdata + " " + entry + "<<<END_BEHAVIOR_TREE>>>"
    return pdata


def reverse_engineer_dataset():
    dataset_file = 'labelled_dataset_594.json'

    dir = os.path.dirname(os.path.abspath(__file__))
    dataset_file_path = os.path.join(dir, dataset_file)

    print(dataset_file_path)

    #check if file exists and is not empty
    if not os.path.exists(dataset_file_path):
        raise FileNotFoundError(f"File not found: {dataset_file_path}")
    
    if os.path.getsize(dataset_file_path) == 0:
        raise ValueError(f"File is empty: {dataset_file_path}")

    #read dataset
    with open(dataset_file_path, 'r') as file:
        data = json.load(file)

    #check if it has a list
    if isinstance(data, list):
        items = data

    #loop through the list and translate each object
    for entry in items:
        print(f"Processing entry: {entry['entry']} ...")
        full_prompt = build_prompt(entry['BT'])
        #print(full_prompt)
        entry['mrq'] = call_openai(full_prompt)
        reverse_engineered.append(entry)

    with open("reverse_engineered_dataset(594).json", "w") as f:
        json.dump(reverse_engineered, f, indent=4)
        print(f"Reverse engineering complete")

reverse_engineer_dataset()