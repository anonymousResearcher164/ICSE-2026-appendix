cycle_7_all_50_bts_final: all 50 rated BTs (cycles 1-5; entries.txt maps id -> original cycle) generated with the FINAL
paper prompt and dictionary (listings/prompt_template.txt + Table I incl. tablenotes, paper state 2026-11-11), embedded
as prompt_template.txt / dictionary.json. Supersedes cycle_7_all_50_bts (rev1), rev6, rev6b_prev_final and the
exploratory rev2-5/7/8/9.

Prompt/dictionary deltas vs. rev6: SequenceStar "start the sequence again from the failed action"; new prompt sentence
"If an action has more than two ports, state only the action in the narrative and list its ports in a final paragraph
beginning with 'Parameters:', one action per sentence." (understandability of port-heavy trees such as 124, 345, 402).

Model:   github-copilot/gpt-5.6-luna via opencode 1.18.35, variant minimal; one fresh `opencode run --pure --agent plan`
         per entry (no tools/file writes, stdin closed); harness message identical to cycle_6/README.txt.
Date:    2026-11-11. Single run per entry (the 15 test BTs 89,108,109,325,371,432,493,515,549,584,124,345,402,475,566
         were generated first as rev9 and copied here unchanged; the other 35 afterwards with identical prompts).
Files:   prompt_entry_N.txt (built from entry_N.xml), entry_N.xml, mrq_entry_N.txt (stdout, ANSI stripped, wrapped at 115),
         raw/ (unmodified stdout/stderr), entries.txt.
Purpose: input for the R1 blinded rating of all 50 descriptions.
Known limitations: deep trees (220, 457, 411, 531, 6) yield 20-50 paragraphs; root paragraphs name composite children by
type/position; run-to-run variance on nested trees (e.g. 371).
