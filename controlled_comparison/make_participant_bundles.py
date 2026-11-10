"""
make_participant_bundles.py

Builds one folder per participant under study material/bt_printouts/,
holding the behavior-tree printouts that participant is allocated.

Blocks are kept apart
---------------------
A participant works through three blocks and the supporting material
changes between them, so the trees for each block go in their own
subfolder rather than in one pile. Handing someone the G2 tree during the
no-guidance block would contaminate the condition, and a flat folder of
four PDFs makes that easy to do by accident.

Printouts are copied from the already-rendered per-group bundles rather
than re-rendered, so a participant's copy of a tree is byte-identical to
the group copy and the two cannot drift apart.

Run after assign_participants.py and after the group bundles exist:

    python3 make_participant_bundles.py
    python3 make_participant_bundles.py --dry-run
"""

import json
import pathlib
import shutil
import sys

ALLOCATION = "task_allocation.json"
PRINTOUTS = pathlib.Path("study material/bt_printouts")
BLOCKS = {
    "G0": "block1_G0_no_guidance",
    "G1": "block2_G1_partial_guidance",
    "G2": "block3_G2_full_guidance",
}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    alloc = json.loads(pathlib.Path(ALLOCATION).read_text())["allocation"]

    missing = [g for g in BLOCKS if not (PRINTOUTS / g).is_dir()]
    if missing:
        raise SystemExit(
            f"no rendered printouts for {', '.join(missing)}. Run "
            f"visualize_trees.py --source <G> --out '{PRINTOUTS}/<G>' first."
        )

    written = problems = 0
    for participant in sorted(alloc):
        dest = PRINTOUTS / participant
        lines = [f"{participant} - behavior tree printouts", ""]
        count = 0
        for group, block in BLOCKS.items():
            entries = sorted(alloc[participant][group],
                             key=lambda n: int(n.split("_")[1]))
            lines.append(f"{block}:")
            for name in entries:
                src = PRINTOUTS / group / f"{name}.pdf"
                if not src.is_file():
                    print(f"  MISSING {src}")
                    problems += 1
                    continue
                count += 1
                lines.append(f"  {name}.pdf")
                if not dry_run:
                    (dest / block).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest / block / f"{name}.pdf")
            lines.append("")
        lines.append(f"total: {count} behavior trees")
        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "contents.txt").write_text("\n".join(lines), encoding="utf-8")
        written += count
        blocks = " | ".join(
            f"{g}: {', '.join(str(int(n.split('_')[1])) for n in sorted(alloc[participant][g], key=lambda n: int(n.split('_')[1])))}"
            for g in BLOCKS)
        print(f"  {participant}  {count} trees   {blocks}")

    print(f"\n{written} printouts placed across {len(alloc)} participants"
          + (" (dry run, nothing written)" if dry_run else ""))
    if problems:
        print(f"{problems} missing source printout(s)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
