# Descriptions

One natural-language description per corpus entry, named `entry_<N>.txt`,
rendered from the node dictionary for the matching `trees/entry_<N>_gt.xml`.

Gate them before use:

    python3 port_coverage_lint.py --batch trees/ descriptions/

The lint fails if a description drops a load-bearing port value from the
ground-truth tree (an attempt count, retry budget, cycle count, threshold,
duration, rate or distance). Run `--inventory trees/` to see which ports
the dictionary has to be able to express.

This directory is intentionally empty of descriptions: populate it from
your own dictionary rendering rather than from hand-written text, so the
coverage number measures the dictionary and not the author.
