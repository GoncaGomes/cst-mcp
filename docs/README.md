# Documentation

Use the local Markdown documents for setup, tool contracts and capability procedures.
The [project README](../README.md) is the repository entry point.

## Current instructions

- [Agent setup and acceptance](AGENT_SETUP.md) covers installation, stdio configuration and checks.
- [Markdown tool reference](TOOLS.md) describes the tool catalog.
  Confirm current input contracts through MCP tool discovery.
- [Utility scripts](../scripts/README.md) covers installed-help, modeler and saved-result checks.
- [Project, geometry and materials](../capabilities_test/01_project_geometry_materials/README.md)
  documents Batch 01 and its known limitations.
- [Parameter-driven geometry](../capabilities_test/02_parameters/README.md)
  documents supported inputs, owned workspaces, reset rules and measurement coverage.
- [Parameter implementation review](../capabilities_test/02_parameters/REVIEW.md)
  records changes and offline checks.
- [Boolean operations and error handling](../capabilities_test/03_booleans/README.md)
  documents operations, error cases and workspace rules.
- [Boolean implementation review](../capabilities_test/03_booleans/REVIEW.md)
  records the reviewed live operations result and later changes.

The user confirmed the agreed modeling scenarios and manual checks.
Some preserved summaries retain older pending manual wording.
That wording is preserved evidence, not a reversal of user confirmation.
Automated readbacks establish only the properties measured by each scenario.
The latest boolean operations summary is preflight.
An earlier documented live invocation completed successfully.
The errors stage has a completed live report with nine passing cases.
See [validation status](../README.md#validation-status) for the full overview.

## Current assessments

- [API and capability assessment](API_REVIEW.md) describes implemented input contracts,
  completed campaign evidence and measurement limits.
- [Reliability assessment](RELIABILITY_REVIEW.md) describes server safeguards,
  capability-client ownership, timeout handling and transport preservation.

These reports distinguish offline checks, native readbacks and manual confirmation.
They do not establish live correctness for every tool or simulation accuracy.
