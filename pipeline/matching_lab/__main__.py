import argparse
import json
from pathlib import Path

from .audits import stress
from .core import evaluate_agent, evaluate_human, freeze

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=("freeze", "human", "stress", "agent"))
parser.add_argument("--root", type=Path, default=Path.cwd())
parser.add_argument("--research", type=Path)
args = parser.parse_args()
if args.action == "agent" and not args.research:
    parser.error("Agent audit requires --research")
if args.action == "freeze":
    result = freeze(args.root)
    print(
        json.dumps(
            {k: result[k] for k in ("candidate_pairs", "training_cases", "full_candidate_routes")}
        )
    )
elif args.action == "human":
    print(json.dumps(evaluate_human(args.root)["evaluation"]))
elif args.action == "agent":
    print(json.dumps(evaluate_agent(args.root, args.research)["agreement"]))
else:
    result = stress(args.root)
    print(
        json.dumps(
            {
                "reversal_cases": result["pair_reversal_cases"],
                "perturbations": result["perturbations"],
                "cluster_counts": {
                    k: v for k, v in result["cluster_audit"].items() if k != "components"
                },
                "blocking_counts": {
                    k: v for k, v in result["blocking_audit"].items() if k != "pairs"
                },
            }
        )
    )
