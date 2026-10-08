"""``campaign`` — the VEP 116.2 merged-cache port campaign (#226, #235).

Entry point: ``./campaign {port,check,report}`` at the repository root
(``python -m campaign``). Modules:

* :mod:`campaign.model` — manifest and settings loading, :class:`Status`,
  the focus tagged union, atomic writes, :class:`CampaignError`;
* :mod:`campaign.focus` — pure focus evaluation on ``vcf_records`` records;
* :mod:`campaign.classify` — pure ``./run_tests --via-cli`` classification;
* :mod:`campaign.port` — crash-safe orchestration with an injected runner;
* :mod:`campaign.check` — independent invariants, one function each;
* :mod:`campaign.report` — the campaign README table.
"""
