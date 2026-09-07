"""Cernora reference workflow and Priority 4 batch normalization companion."""

from cernora_reference_workflow.batch_summary import (
    normalize_execution_pack,
    summarize_execution_pack,
)
from cernora_reference_workflow.comparison_input import (
    ComparisonConfigurationError,
    assemble_comparison_input,
    assemble_comparison_input_from_paths,
    compare_batch_summary,
)
from cernora_reference_workflow.comparison_plan import (
    ComparisonPlanV1,
    TreatmentDeclaration,
    materialize_comparison_plan,
    materialize_treatment_declaration,
)
from cernora_reference_workflow.controlled_experiment_spec import (
    ACCEPTED_CORE_0_1_4_WHEEL_SHA256,
    ControlledExperimentSpecV2,
    DatasetAuthority,
    DatasetCaseAuthority,
    StatisticalPolicy,
    materialize_controlled_experiment_spec,
    materialize_dataset_authority,
    materialize_statistical_policy,
)
from cernora_reference_workflow.controlled_run_plan import (
    ControlledRunPlanV2,
    materialize_controlled_run_plan,
)
from cernora_reference_workflow.experiment_spec import ExperimentSpec
from cernora_reference_workflow.run_plan import RunPlan

__all__ = [
    "ACCEPTED_CORE_0_1_4_WHEEL_SHA256",
    "ComparisonConfigurationError",
    "ComparisonPlanV1",
    "ControlledExperimentSpecV2",
    "ControlledRunPlanV2",
    "DatasetAuthority",
    "DatasetCaseAuthority",
    "ExperimentSpec",
    "RunPlan",
    "StatisticalPolicy",
    "TreatmentDeclaration",
    "assemble_comparison_input",
    "assemble_comparison_input_from_paths",
    "compare_batch_summary",
    "materialize_comparison_plan",
    "materialize_controlled_experiment_spec",
    "materialize_controlled_run_plan",
    "materialize_dataset_authority",
    "materialize_statistical_policy",
    "materialize_treatment_declaration",
    "normalize_execution_pack",
    "summarize_execution_pack",
]
__version__ = "0.4.2"
