"""Reproduce the observed Serper country validation failure without network or keys."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts", type=Path, required=True, help="Evaluated plugin scripts directory")
    args = parser.parse_args()
    sys.path.insert(0, str(args.scripts.resolve()))
    import search_canonical
    results = []
    with patch("socket.socket", side_effect=AssertionError("network forbidden")):
        for country in ("France", "fr", "FR"):
            try:
                value = search_canonical.search_operation_args(query="synthetic indoor venue", country=country,
                                                              language="fr", limit=10, mode="web")
                results.append({"country": country, "status": "OK", "canonical_country": value["params"]["country"]})
            except ValueError as exc:
                results.append({"country": country, "status": "BLOCKED", "reason": str(exc)})
    assert results[0]["reason"] == "IDENTITY_VALUE_INVALID: country"
    assert all(r["canonical_country"] == "FR" for r in results[1:])
    digest = hashlib.sha256(Path(search_canonical.__file__).read_bytes()).hexdigest()
    print(json.dumps({"network_calls": 0, "credential_access": False,
                      "source_module_sha256": digest, "results": results}, indent=2))


if __name__ == "__main__":
    main()
