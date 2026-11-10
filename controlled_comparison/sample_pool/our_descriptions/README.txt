cycle_7_all_50_bts: all 50 rated BTs (cycles 1-5, see entries.txt for the cycle each BT came from) generated with the
FINAL paper prompt and dictionary (= cycle_6_minimal1 artifacts: listings/prompt_template.txt + Table I incl. tablenotes,
paper state 2026-11-09; dictionary.json / prompt_template.txt copied here). Example block as in cycle 5.

Model:   github-copilot/gpt-5.6-luna via opencode 1.18.35, variant minimal; one fresh `opencode run --pure --agent plan`
         per entry (no tools/file writes), stdin closed; harness message identical to cycle_6/README.txt.
Date:    2026-11-09
Files:   prompt_entry_N.txt (built from entry_N.xml), entry_N.xml, mrq_entry_N.txt (stdout, ANSI stripped, wrapped at 115),
         raw/ (unmodified stdout/stderr), entries.txt (id -> original cycle).
Purpose: input for the R1 blinded re-rating of all 50 descriptions with the revised artifacts.
