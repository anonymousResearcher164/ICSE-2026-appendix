import json

# Load JSON from a file
with open("btgenbot_dataset.json", "r") as f:
    data = json.load(f)

count = 0
labelled_data = []

for item in data:
    output = item.get("output")
    count = count + 1
    labelled_data.append({"entry": count, "BT": output})
    
with open("labelled_dataset_594.json", "w") as f:
    json.dump(labelled_data, f, indent=4)

print(f'{count} BTs labelled and written to labelled_dataset_594.json')