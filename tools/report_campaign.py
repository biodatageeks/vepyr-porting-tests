"""Render the per-candidate status table without hiding queued or failing tests."""

import collections
import json

from port_campaign import MANIFEST


def main():
    cases = json.loads(MANIFEST.read_text())
    counts = collections.Counter(c["status"] for c in cases)
    lines = [
        "# VEP 116.2 merged-cache ports",
        "",
        "One PR; one commit and report per ten ports. SNVs precede small indels.",
        "",
        (
            "[Full 2,103-assertion audit](../vep1162-assertion-audit/README.md) · "
            "[Failure details and actual outputs](FAILURES.md) · "
            "[Input/reference audit](reference-audit.json)"
        ),
        "",
        "Input normalization reverified for "
        + str(
            sum(c.get("result", {}).get("normalization_verified", False) for c in cases)
        )
        + " executed tests: one input SHA-256 shared by VEP Docker and vepyr.",
        "",
        "Current results: "
        + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        + ".",
        "",
        (
            "PASS means whole VCF body equality. Focus records the single primary "
            "property; the body comparison also checks incidental fields. "
            "QUEUED and BLOCKED are not ported tests."
        ),
        "",
        (
            "| Test | Description | Status | Focus | Old-cache problem | "
            "Potential vepyr bug | Unsupported feature | Porting note | "
            "Source test | Implementation |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def escape(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    for c in cases:
        name = c["id"]
        if "result" in c:
            name = f"[{name}](../../../tests/data/{c['directory_name']}/test.toml)"
        result = c.get("result", {})
        focus = (
            "PASS"
            if result.get("focus_pass")
            else ("FAIL" if "focus_pass" in result else "—")
        )
        fields = [
            name,
            c["description"],
            c["status"],
            focus,
            c["old_cache_problem"],
            c["potential_vepyr_bug"],
            c["unsupported_features"],
            c.get("block_reason", c.get("witness_note", "")),
            " ".join(
                f"[assertion {i + 1}]({url})" for i, url in enumerate(c["source_links"])
            ),
            " ".join(
                f"[{e['function']}]({e['url']})" for e in c["implementation_links"]
            ),
        ]
        lines.append("| " + " | ".join(escape(v) for v in fields) + " |")
    MANIFEST.with_name("README.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
