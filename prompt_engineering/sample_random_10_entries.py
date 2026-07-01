import random
import json
import os

number_of_cycles = 5 

folder_name = "experiment_samples"

with open("labelled_dataset_594.json", "r") as f:
    data = json.load(f)

for i in range(number_of_cycles):
    numbers = random.sample(range(1, 595), 10)
    print(f'cycle:{i+1}')
    print("Randomly sampled entries:", numbers)

    for obj in data:
        entry = obj.get("entry")
        entry = int(entry)

        if entry in numbers:
            #check if experiement_samples folder exists
            os.makedirs(folder_name, exist_ok=True)
            #build name of entry file
            build_txt_name = f'entry_{entry}'

            #build cycle folder name
            child_folder = f'cycle_{i+1}'
            #check if cycle folder exists. if not create
            embedded_folder_path = os.path.join(folder_name, child_folder)
            os.makedirs(embedded_folder_path, exist_ok=True)
            
            #build full file path and write BT to file
            full_file_path = os.path.join(embedded_folder_path, build_txt_name)
            with open(full_file_path, "w") as f:
                f.write(obj.get("BT"))



