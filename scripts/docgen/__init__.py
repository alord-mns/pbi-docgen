"""Model-agnostic Power BI documentation generator.

Portable across Power BI repositories: all solution-specific facts live in
``model-docs/.docgen.toml``, never in this package.

Modules:
    config      — load the per-repo ``.docgen.toml``
    tmdl        — parse TMDL semantic-model files
    pbir        — parse PBIR report definition files
    dataflow    — parse exported Power BI dataflow JSON
    orchestration — parse Power Automate / Logic App workflow JSON
    power_apps  — parse unpacked canvas Power Apps
    sqlsource   — parse SQL view exports
    lineage     — build the end-to-end dependency graph
    doctor      — preflight check for a new repository
    generate    — emit the Markdown card knowledge base
    validate    — enforce the quality gates in scripts/docgen/documentation_req.md
"""

__version__ = "1.2.0"
