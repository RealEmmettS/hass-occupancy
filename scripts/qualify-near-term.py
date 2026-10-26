"""Run the production near-term gate and serving path on a private snapshot."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "occupancy-forecast-edge"))
from occupancy_forecast import config, near_term
from occupancy_forecast.sources.ha import StoreSource
from occupancy_forecast.sources.store import HistoryStore

parser = argparse.ArgumentParser(__doc__)
for name in ("config", "history", "output"):
    parser.add_argument("--" + name, type=Path, required=True)
parser.add_argument("--end", required=True)
args = parser.parse_args()
config.configure(config.Settings.from_json(args.config.read_text()))
source = StoreSource(HistoryStore(args.history), None)
summary = near_term.train_all(source, args.output, stop=args.end)
(args.output / "summary.json").write_text(json.dumps(summary, indent=2))
artifact = near_term.load(args.output)
result = near_term.forecast(artifact, source, pd.Timestamp(args.end))
(args.output / "forecast.json").write_text(json.dumps(result, indent=2))
print(json.dumps({"duration_s": summary["duration_s"], "qualified": {
    m: [s for s, v in by_subject.items() if v["ship"]]
    for m, by_subject in summary["metrics"].items()}}, indent=2))
