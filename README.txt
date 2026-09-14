Stage31 regression fix

Replace:
app/models/runs.py

Reason:
Stage31 overwrote the Stage25 PipelineRun model. This replacement preserves
PipelineRun/RunStatus for pipeline persistence and adds the Stage31 SEORun model.
