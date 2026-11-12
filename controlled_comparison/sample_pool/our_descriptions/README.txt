cycle_7_all_50_bts_final: all 50 rated BTs (cycles 1-5; entries.txt maps id -> original cycle) generated with the FINAL
paper prompt and dictionary (listings/prompt_template.txt + Table I incl. tablenotes, paper state 2026-11-12), embedded
as prompt_template.txt / dictionary.json. Supersedes cycle_7_all_50_bts (rev1), rev6, rev6b_prev_final, rev9b_prev_final
and the exploratory rev2-5/7/8/9/10.

Prompt/dictionary deltas vs. rev6: SequenceStar "start the sequence again from the failed action"; new prompt sentence
"State only the actions in the narrative and list the ports of every action that has ports in one final paragraph
beginning with 'Parameters:', one sentence per action." (understandability: port lists moved out of the narrative;
earlier threshold variants (">2 ports", rev9/rev9b) lost ports of small actions in Nav2 trees and were rejected).

Model:   github-copilot/gpt-5.6-luna via opencode 1.18.35, variant minimal; one fresh `opencode run --pure --agent plan`
         per entry (no tools/file writes, stdin closed); harness message identical to cycle_6/README.txt.
Date:    2026-11-12. Single run per entry, all 50 generated in one pass with identical prompts. raw/nodetype_noun_run/ holds a
         later pass with an additional [Actions] clause ("using the node type as the noun") that was REJECTED: it fixed generic
         nouns (376 "the parallel task") but exploded paragraph counts (411: 18 -> 80, 402: 10 -> 20).
Files:   prompt_entry_N.txt (built from entry_N.xml), entry_N.xml, mrq_entry_N.txt (stdout, ANSI stripped, wrapped at 115),
         raw/ (unmodified stdout/stderr), entries.txt.
Purpose: input for the R1 blinded rating of all 50 descriptions.
Known limitations: a few unnamed composites are referred to by a generic noun (376 "the parallel task"); deep trees (220, 457, 411, 531, 6) yield 20-40 paragraphs; root paragraphs name composite children by
type/position; run-to-run variance on nested trees (e.g. 371).
