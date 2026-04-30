"""Power BI documentation generator for the FH&B Weekly solution.

Modules:
    tmdl      — parse TMDL semantic-model files
    pbir      — parse PBIR report definition files
    dataflow  — parse exported Power BI dataflow JSON
    lineage   — build the end-to-end dependency graph
    generate  — emit Markdown documentation
    validate  — enforce quality gates from docs/documentation_req.md §4
"""
