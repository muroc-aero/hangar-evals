"""Exit 0 if the given run config's (case, model) already has a completed
summary with at least `seeds` seeds attempted, so a chunk script can skip it.
Exit 1 otherwise. A graded FAIL still counts as attempted (we skip it) — use
the direct runner's --resume to retry timed-out seeds deliberately."""
import json, sys, glob, os

cfg = json.load(open(sys.argv[1]))
case, model, seeds = cfg["case"], cfg.get("model"), cfg["seeds"]
results = cfg.get("results_dir", "results")
for f in glob.glob(os.path.join(results, f"{case}_*_summary.json")):
    try:
        rows = json.load(open(f))
    except Exception:
        continue
    for r in rows:
        if (r.get("case") == case
                and (model is None or r.get("model") == model)
                and r.get("n_seeds", 0) >= seeds):
            print(f)
            sys.exit(0)
sys.exit(1)
