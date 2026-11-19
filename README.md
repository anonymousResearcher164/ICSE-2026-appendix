## Replication package: From Robotic Mission Requirements to Behavior Models and Back Again (ICSE)

This package holds the data, scripts and study material behind the paper. The
work runs in both directions: *forward* from a natural-language mission
description to a BehaviorTree.CPP model, and *backward* from a BehaviorTree.CPP model to a
natural-language mission description of what it does.

- **594 behavior trees**, taken from our open-source dataset, used for the
  large-scale reverse-engineering run (`datasets/`). Same dataset was used in th BTGenBot project.
- **50 of those trees**, a sample used for everything that needed human
  effort or exhaustive checking. The 50 entries are the same files in
  `controlled_comparison/sample_pool/Ground_truth_BTs/` and
  `bt_trace_eq_checker/gt/` (byte-identical), so entry numbers are
  comparable across the two folders.

Entry numbers (`entry_108`, `entry_6`, …) are the stable identifiers assigned in
`datasets/labelled_dataset_594.json` and used as filenames everywhere else.

## `datasets/`

The 594-tree corpus and the script that reverse-engineers descriptions from
it. This is the input to the quantitative half of the paper.

| File                                   | What it is                                                                                                                                                                                                                                                                            |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `btgenbot_dataset.json`                | The upstream **BTGenBot dataset**, unmodified: 594 objects with keys `instruction`, `input`, `output`. `output` is the behavior tree as BehaviorTree.CPP XML; `input` is BTGenBot's own natural-language description of it. This is the external baseline the paper compares against. |
| `labelled_dataset_594.json`            | The same 594 trees, stripped to keys `entry` (integer, 1–594) and `BT` (the XML). Produced by `prompt_engineering/extract_and_label.py`, which copies the `output` field and numbers it. Its purpose is to give every tree a stable id.                                               |
| `reverse_engineered_dataset(594).json` | The main output: keys `entry`, `BT`, `mrq`. `mrq` is the mission description generated from the tree. All 594 entries are populated. This was reverse-engineered with `prompt_engineering/dictionary_v6.json`                                                                                                                                                 |
| `reverse_engineer_dataset.py`          | Script used to generate the above. Walks `labelled_dataset_594.json`, prompts a model once per tree, and writes the result in `mrq` field.                                                                                                                                            |
| `helper.txt`                           | The prompt preamble prepended to every tree when generating with the script above.                                                                                                                                                                                                    |

## `bt_trace_eq_checker/`

Checks whether a synthesized tree behaves the same as the ground-truth tree
it was meant to reproduce. The checker enumerates leaf outcomes, collects the
resulting execution traces from both trees, and compares them, so a verdict
is about *behavior* rather than about XML text.

| Path                                                                         | Contents                                                                                                                                                             |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bt_trace_equivalence.py`                                                    | The checker: XML parsing, scenario enumeration, trace collection, equivalence verdict.                                                                               |
| `main.py`                                                                    | Driver over all 50 pairs. Flags: `--mode {auto,exhaustive,sample}`, `--max-leaves`, `--ignore-name-style`, `--output`.                                               |
| `gt/`                                                                        | The 50 ground-truth trees(`entry_N.xml`).                                                                                                                            |
| `om/`, `ha/`, `btg/`                                                         | The 50 trees synthesized from our descriptions, from human-authored descriptions, and from BTGenBot's descriptions. One tree per ground-truth entry, same filenames. |
| `report_our_methods.txt`, `report_human_authored.txt`, `report_btgenbot.txt` | Checker output for each of the three sources. Each opens with a summary cross-tabulating the verdict against whether it was decided exhaustively or by sampling.     |

```bash
python3 bt_trace_eq_checker/main.py --mode auto --max-leaves 16
```

`auto` enumerates every leaf-outcome combination when the tree is small
enough and falls back to sampling when it is not, which is why the reports
distinguish the two: an `equivalent` verdict reached by sampling means no
counterexample was found, not that none exists.

---

## `prompt_engineering/`

The iterative development of the node dictionary and the synthesis prompt.

| Path                                                                               | Contents                                                                                                                            |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `dictionary_v1.json` … `dictionary_v6.json`                                        | Six successive revisions of the node dictionary mapping BehaviorTree.CPP node types to natural-language phrasings. `v6` is current. |
| `experiment_samples/cycle_1/` … `cycle_5/`                                         | Five prompt-refinement cycles, 30 files each (10 BTs, 10 prompts for each BT, 10 descriptions(the output)).                         |
| `experiment_samples/consolidated_cycle`                                            | Prompts and results(descriptions) derived from the final version of prompt refinements paired with dictionary v6.                   |
| `synthesis/` | Ground-truth BTs from the sample of 50 as well as synthesized BTs from 3 sources, and the synthesis prompt template.     |
| `sample_random_10_entries.py`                                                      | Draws the random sub-sample.                                                                                                        |
| `synthesis/Synthesized_from_our_descriptions/`                                     | Trees synthesized back from our descriptions, `entry_N.xml`. Same files the checker reads as `om/`.                                 |
| `synthesis/Synthesized_from_human_authored/`                                       | Trees synthesized back from the human-authored descriptions. The checker's `ha/`.                                                   |
| `synthesis/Synthesized_BTGenbot_descriptions/`                                     | Trees synthesized back from BTGenBot's descriptions. The checker's `btg/`.                                                          |
| `synthesis/Ground_truth_BTs/`                                                      | The 50 reference trees, `entry_N.xml`.                                                                                              |

---

## `controlled_comparison/`

The study comparing description sources.

`mission_descriptions(3 sources)/` holds mission descriptions from 3 sources: BTGenBot, human-authored and descriptions generated with our dictionary, prompts, catalog

`mission_descriptions(3 sources)/our_descriptions` descriptions generated with our methods. Contains the prompts used to derive them. This is a direct copy of `prompt_engineering/experiment_samples/consolidated_cycle`

`mission_descriptions(3 sources)/Human_authored_descriptions` descriptions authored by humans (19 experiment participants)

`mission_descriptions(3 sources)/BTGenBot_descriptions` descriptions sourced from the BTGenBot dataset

`study material/` is what participants actually received: printouts of the sampled BTs allocated to participants and the experiment instructtions given to participants i.e., `instructions.pdf`,

---

## Repository root

| File             | Purpose                    |
| ---------------- | -------------------------- |
| `datasheet.xlsx` | The dataset for the paper. |
