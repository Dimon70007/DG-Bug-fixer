"""Build references/invariant-matrix.json for DoD-13 (development helper, not shipped).

Reads spec §13, pairs every invariant with the tests that hold it, and refuses to write
anything unless the mapping is complete in both directions: every invariant is named, and
every test in the plugin's suite is named by an invariant or by a support requirement.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve()
PLUGIN = HERE.parents[2]
REPO = HERE.parents[4]
SPEC = REPO / "docs" / "bug-fixer-v3-spec.md"
TESTS = PLUGIN / "tests"

# invariant id -> (module stem, test name) pairs; module stem omits the `test_bugfixer_` prefix
MAP: dict[str, list[str]] = {
    "1": [
        "fsm::test_there_are_exactly_eight_named_edges",
        "fsm::test_both_4_to_3_edges_coexist",
        "fsm::test_edge_pair_with_two_meanings_requires_a_name",
        "fsm::test_edges_subcommand_lists_all_eight",
        "fsm::test_unknown_edge_name_for_a_valid_pair_is_refused",
        "fsm::test_phase_nine_is_not_a_phase",
        "fsm::test_skipping_from_phase_1_is_refused",
        "fsm::test_there_is_no_edge_back_to_phase_1",
        "fsm::test_there_is_no_edge_from_phase_5_to_research",
        "fsm::test_history_records_the_edge_name",
        "cli_v3::test_state_lists_the_edges_and_reads_the_state",
        "skill::test_all_eight_edges_are_named",
        "skill::test_both_4_to_3_edges_are_distinguished",
        "skill::test_fsm_surface_is_the_eight_edge_automaton",
    ],
    "2": [
        "fsm::test_reproduced_needs_a_red_test",
        "fsm::test_reproduced_needs_a_baseline",
        "fsm::test_reproduced_needs_clarification",
        "fsm::test_three_pairs_of_questions_are_enough",
        "fsm::test_assumptions_allow_a_headless_run",
        "fsm::test_two_pairs_without_a_gap_line_are_refused",
        "fsm::test_two_pairs_with_a_gap_line_are_enough",
        "fsm::test_first_guess_requires_exactly_one_h0",
        "fsm::test_first_guess_succeeds_with_a_complete_h0",
        "fsm::test_two_h0_blocks_are_refused",
        "fsm::test_first_guess_requires_proposed_to_point_at_a_record",
        "fsm::test_first_guess_is_refused_when_an_attempt_exists",
        "fsm::test_already_tried_needs_a_previous_attempt",
        "fsm::test_already_tried_succeeds_after_an_attempt",
        "fsm::test_already_tried_is_refused_when_the_task_is_already_solved",
        "fsm::test_hypothesis_ready_needs_an_unchecked_hypothesis",
        "fsm::test_hypothesis_ready_succeeds",
        "fsm::test_proven_succeeds_with_every_proof",
        "fsm::test_proven_names_each_missing_marker",
        "fsm::test_proven_requires_the_results_table",
        "fsm::test_proven_requires_a_second_test_or_a_named_corner_case",
        "fsm::test_one_test_is_enough_when_the_corner_case_is_named",
        "fsm::test_proven_requires_an_attempt",
        "fsm::test_transition_needs_evidence",
        "templates_v3::test_workflow_results_table_has_the_required_columns",
        "harness::test_headless_prompt_demands_explicit_assumptions",
    ],
    "3": [
        "fsm::test_cap_reached_refuses_both_4_to_3_edges",
        "fsm::test_limit_reached_is_a_valid_status",
        "gate::test_attempt_cap_denies_writes",
        "gate::test_below_the_attempt_cap_allows_phase4_writes",
        "gate::test_limit_reached_denies_writes",
        "limits_v3::test_twelve_attempts_exceed_the_cap",
        "harness::test_attempt_cap_writes_limit_reached",
        "harness::test_cap_stops_the_loop",
        "skill::test_counters_are_a_and_rechecks",
    ],
    "4": [
        "ledger_v3::test_attempts_are_counted",
        "limits_v3::test_attempt_gap_is_named",
        "limits_v3::test_attempts_without_a_gap_pass",
    ],
    "5": [
        "ledger_v3::test_hypothesis_count_includes_duplicates",
        "limits_v3::test_recheck_gap_is_named_with_its_hypothesis",
        "limits_v3::test_sixth_recheck_is_refused",
        "limits_v3::test_recheck_without_a_probe_is_refused",
        "limits_v3::test_eleven_hypotheses_are_refused",
        "limits_v3::test_ten_hypotheses_are_allowed",
    ],
    "6": [
        "ledger_v3::test_rechecks_are_counted_per_hypothesis",
        "limits_v3::test_rechecks_do_not_consume_attempts",
    ],
    "7": [
        "ledger_v3::test_counters_restart_after_an_operator_reset",
        "ledger_v3::test_agent_written_reset_is_refused",
        "ledger_v3::test_reset_without_a_reason_is_refused",
        "limits_v3::test_unauthorized_reset_is_reported_as_a_problem",
        "limits_v3::test_operator_reset_restarts_the_numbering",
    ],
    "8": [
        "limits_v3::test_rewriting_an_old_record_is_allowed",
        "limits_v3::test_no_integrity_module_exists",
    ],
    "9": [
        "limits_v3::test_task_fingerprint_is_recorded_on_first_sight",
        "limits_v3::test_task_drift_warns_but_does_not_refuse",
        "limits_v3::test_clarifications_do_not_count_as_task_drift",
    ],
    "10": [
        "ledger_v3::test_status_is_derived_from_records_not_from_the_field",
    ],
    "11": [
        "ledger_v3::test_hypotheses_without_any_block_is_empty_not_an_error",
        "ledger_v3::test_rank_and_metadata_are_parsed",
        "ledger_v3::test_unchecked_hypotheses_come_in_rank_order",
        "ledger_v3::test_records_parse_required_fields",
        "ledger_v3::test_record_body_keeps_the_markers",
        "skill::test_template_defines_the_finding_block",
        "skill::test_hypothesis_template_defines_the_subtask_block",
        "templates_v3::test_finding_template_parses_as_a_record",
        "templates_v3::test_hypothesis_template_parses_as_a_hypothesis",
        "templates_v3::test_templates_use_the_long_dash_in_headings",
    ],
    "12": [
        "fsm::test_first_guess_requires_the_working_on_marker",
        "fsm::test_hypothesis_ready_refuses_two_markers",
        "gate::test_phase4_allows_production_writes_with_an_active_hypothesis",
        "gate::test_phase4_denies_a_write_without_an_active_hypothesis",
        "gate::test_phase4_denies_a_write_naming_a_missing_hypothesis",
        "ledger_v3::test_canonical_ledger_has_exactly_one_active_marker",
        "ledger_v3::test_no_marker_is_empty_string",
        "ledger_v3::test_two_markers_are_ambiguous",
        "ledger_v3::test_marker_with_trailing_noise_does_not_match",
        "ledger_v3::test_marker_inside_prose_is_not_a_marker",
        "skill::test_working_on_is_one_pointer",
    ],
    "13": [
        "cursor_payloads_v3::test_the_suite_command_is_allowed_in_phase_one",
        "cursor_payloads_v3::test_a_real_write_is_still_a_write",
        "cursor_payloads_v3::test_a_production_write_is_still_refused_in_phase_one",
        "deploy_v3::test_installed_hook_denies_a_production_write",
        "deploy_v3::test_installed_hook_allows_a_test_write",
        "emergency_switch_v3::test_the_switch_turns_off_the_phase_rule_for_this_ledger",
        "emergency_switch_v3::test_the_switch_does_not_disable_the_gate",
        "emergency_switch_v3::test_the_switch_is_not_a_default_anywhere",
        "fail_loud::test_creating_the_ledger_does_not_open_production_writes",
        "fsm::test_phase_gate_off_allows_tooling_ledgers",
        "fsm::test_phase_gate_off_does_not_unlock_a_terminal_status",
        "gate::test_phase1_allows_test_writes",
        "gate::test_phase1_denies_production_writes",
        "gate::test_phase1_allows_running_tests",
        "gate::test_phase1_denies_shell_writes",
        "gate::test_phase1_allows_creating_a_directory_under_the_test_paths",
        "gate::test_phase3_denies_production_writes",
        "gate::test_blocked_status_denies_even_test_writes",
        "gate::test_done_status_denies_writes",
        "gate::test_partial_is_terminal_for_the_automaton",
        "gate::test_partial_does_not_override_a_terminal_status",
        "gate::test_production_writes_stay_denied_outside_phase_4",
    ],
    "14": [
        "legacy_v3::test_iteration_heading_without_hypotheses_is_legacy",
        "legacy_v3::test_three_zero_ledger_is_not_legacy",
        "legacy_v3::test_iteration_field_in_a_block_is_not_a_heading",
        "legacy_v3::test_empty_ledger_is_not_legacy",
        "legacy_v3::test_fresh_three_zero_ledger_is_not_legacy",
        "legacy_v3::test_three_zero_record_without_hypotheses_is_not_legacy",
        "legacy_v3::test_prose_mention_of_iteration_is_not_a_heading",
        "legacy_v3::test_legacy_ledger_denies_a_write_with_an_actionable_reason",
        "legacy_v3::test_legacy_ledger_is_refused_for_reads_too",
        "legacy_v3::test_migrated_ledger_is_accepted",
        "legacy_v3::test_cli_reports_the_legacy_refusal",
    ],
    "15": [
        "fsm::test_unknown_status_is_refused",
        "gate::test_unknown_status_is_denied_not_read_as_running",
        "gate::test_status_mentioned_in_prose_is_not_a_status",
        "gate::test_state_cli_rejects_bad_values",
    ],
    "16": [
        "skill::test_required_sections_present",
        "skill::test_no_three_phase_scheme_survives",
        "templates_v3::test_workflow_does_not_teach_the_three_phase_cycle",
    ],
    "17": [
        "skill::test_every_record_marker_has_a_format_row",
    ],
    "17a": [
        "ledger_v3::test_canonical_ledger_parses_with_every_reader",
        "ledger_v3::test_canonical_ledger_derives_the_documented_statuses",
        "templates_v3::test_workflow_example_parses_with_the_ledger_readers",
        "templates_v3::test_workflow_example_satisfies_the_proven_contract",
    ],
    "17b": [
        "fsm::test_markers_in_prose_do_not_satisfy_a_guard",
        "fsm::test_clarification_in_prose_does_not_count",
        "fsm::test_phase_gate_off_in_prose_is_not_an_opt_out",
        "gate::test_phase4_denies_a_table_form_attempt",
        "gate::test_attempt_marker_ignores_prose_and_tables",
        "ledger_v3::test_attempt_inside_prose_is_not_a_probe",
        "ledger_v3::test_attempt_in_a_table_row_is_not_a_probe",
        "ledger_v3::test_table_form_recheck_is_reported",
        "skill::test_table_form_is_named_as_wrong",
    ],
    "18": [
        "fsm::test_proven_requires_a_probe_record_not_a_route",
        "ledger_v3::test_canonical_records_carry_kind_and_scope",
        "ledger_v3::test_canonical_has_a_probe_record_for_the_proven_hypothesis",
    ],
    "19": [
        "terminal_fields_v3::test_report_heading_copied_from_the_spec_is_accepted",
        "terminal_fields_v3::test_status_without_a_report_names_the_report",
    ],
    "20": [
        "fsm::test_hypothesis_ready_needs_a_merge_line",
    ],
    "21": [
        "fsm::test_refutation_without_logs_is_refused_on_both_edges",
    ],
    "22": [
        "fsm::test_deepen_same_succeeds_with_a_delta",
        "fsm::test_deepen_same_needs_three_numbers",
    ],
    "23": [
        "fsm::test_refutation_without_a_revert_is_refused",
        "fsm::test_deepen_same_refuses_a_revert_when_a_delta_exists",
        "fsm::test_deepen_same_case_c_requires_a_revert",
    ],
    "24": [
        "fsm::test_partially_confirmed_cannot_enter_phase_5",
    ],
    "25": [
        "fsm::test_deepen_same_requires_a_resolution_on_a_metrics_conflict",
        "templates_v3::test_workflow_names_the_resolution_rule",
    ],
    "26": [
        "cli_v3::test_secrets_scans_a_file_and_the_diff",
        "init_and_required_contract::test_init_never_copies_mcp_secrets_into_the_contract",
        "limits_v3::test_each_refusal_pattern_fires",
        "limits_v3::test_a_fact_of_setup_is_allowed",
        "limits_v3::test_harmless_flag_is_not_even_a_warning",
        "limits_v3::test_the_refusal_does_not_repeat_the_secret",
        "limits_v3::test_an_env_var_reference_is_not_a_secret",
        "limits_v3::test_a_null_value_is_not_a_secret",
        "limits_v3::test_record_with_a_secret_is_denied",
        "limits_v3::test_clean_record_is_allowed",
        "limits_v3::test_a_record_reporting_a_key_length_is_denied",
        "limits_v3::test_cli_reports_a_secret_in_a_file",
        "limits_v3::test_cli_returns_zero_on_a_clean_file",
        "secret_wordings_v3::test_the_template_exists",
        "secret_wordings_v3::test_the_skill_points_at_the_template",
        "secret_wordings_v3::test_the_template_teaches_something",
        "secret_wordings_v3::test_no_taught_phrase_is_refused",
        "secret_wordings_v3::test_the_forbidden_examples_are_marked_as_such",
        "secret_wordings_v3::test_the_record_markers_stay_copyable",
        "secrets_derived_v3::test_a_derived_characteristic_is_refused",
        "secrets_derived_v3::test_the_derived_rule_names_itself",
        "secrets_derived_v3::test_a_value_is_still_refused",
        "secrets_derived_v3::test_the_fact_of_setup_is_allowed",
        "secrets_derived_v3::test_a_harmless_flag_is_still_silent",
        "secrets_derived_v3::test_an_env_var_reference_is_not_a_secret",
        "secrets_derived_v3::test_a_null_value_is_still_allowed",
        "secrets_derived_v3::test_the_scanner_has_no_advisory_level",
        "secrets_derived_v3::test_the_finding_type_no_longer_carries_an_advisory_flag",
        "secrets_derived_v3::test_the_cli_is_clean_on_a_setup_fact",
        "secrets_derived_v3::test_the_cli_refuses_a_length",
    ],
    "27": [
        "fsm::test_hypothesis_ready_needs_the_active_marker",
        "fsm::test_hypothesis_ready_refuses_a_marker_on_a_refuted_hypothesis",
        "fsm::test_next_in_queue_requires_the_marker_to_move_first",
        "fsm::test_next_in_queue_succeeds_once_the_marker_is_switched",
    ],
    "27a": [
        "fsm::test_next_in_queue_refuses_a_partial_verdict",
        "fsm::test_queue_exhausted_refuses_when_a_hypothesis_is_waiting",
        "fsm::test_queue_exhausted_needs_new_information",
        "fsm::test_queue_exhausted_succeeds_with_an_empty_queue_and_new_information",
    ],
    "27b": [
        "fsm::test_deepen_same_keeps_the_same_hypothesis_active",
        "fsm::test_deepen_same_ignores_the_queue",
    ],
    "27c": [
        "ledger_v3::test_partial_verdict_keeps_the_hypothesis_in_the_queue",
    ],
    "28": [
        "terminal_fields_v3::test_partial_without_remaining_names_the_section",
        "terminal_fields_v3::test_partial_with_an_empty_remaining_names_the_section",
        "terminal_fields_v3::test_remaining_heading_copied_from_the_spec_is_accepted",
        "terminal_fields_v3::test_remaining_as_a_table_is_accepted",
        "terminal_fields_v3::test_remaining_as_a_numbered_list_is_accepted",
    ],
    "29": [
        "terminal_fields_v3::test_blocked_without_a_reason_names_the_reason_field",
        "terminal_fields_v3::test_blocked_without_logs_names_the_logs_field",
        "terminal_fields_v3::test_blocked_with_an_unknown_reason_value_is_refused",
        "terminal_fields_v3::test_well_formed_blocked_still_blocks",
    ],
    "30": [],
    "31": [
        "gate::test_the_task_file_may_be_written_in_every_phase",
        "gate::test_the_task_file_is_recognised_whatever_the_tool",
        "gate::test_the_task_file_is_recognised_below_the_project_root",
        "gate::test_appending_to_the_task_file_from_a_shell_is_allowed",
        "gate::test_a_shell_write_that_also_touches_code_is_still_denied",
        "gate::test_a_same_named_file_elsewhere_is_not_the_ledger",
        "skill::test_ledger_forbids_a_second_file",
    ],
    "32": [
        "fsm::test_idempotent_advance_writes_no_history",
        "journal_v3::test_an_event_is_one_json_line_with_the_required_fields",
        "journal_v3::test_a_record_cannot_carry_a_newline_in_its_reason",
        "journal_v3::test_an_advance_grows_the_journal_by_exactly_one_line",
        "journal_v3::test_a_refused_transition_still_records_nothing_new_about_the_phase",
        "journal_v3::test_a_refused_write_is_recorded_with_the_tool_and_the_reason",
        "journal_v3::test_an_allowed_write_records_no_denial",
    ],
    "33": [
        "cli_v3::test_the_journal_refusal_names_the_plugin_as_the_writer_and_a_runnable_command",
        "gate::test_a_shell_read_of_the_journal_is_allowed",
        "journal_v3::test_an_agent_write_to_the_journal_is_denied",
        "journal_v3::test_an_agent_delete_of_the_journal_is_denied",
        "journal_v3::test_the_refusal_names_the_owner",
    ],
    "34": [
        "cli_v3::test_state_advances_the_run_through_the_cli",
        "cli_v3::test_state_refuses_a_value_outside_the_machine",
        "gate::test_corrupt_sidecar_denies",
        "gate::test_attempts_counted_from_ledger_not_sidecar",
        "gate::test_state_cli_roundtrip",
        "journal_v3::test_the_journal_sits_beside_the_task_file",
        "journal_v3::test_the_phase_comes_from_the_last_advance",
        "journal_v3::test_a_status_event_is_visible_in_the_state",
        "journal_v3::test_a_corrupt_journal_line_fails_closed",
        "journal_v3::test_a_denial_record_does_not_move_the_phase",
        "journal_v3::test_the_old_sidecar_no_longer_moves_the_phase",
        "journal_v3::test_the_gate_reads_the_phase_the_cli_recorded",
    ],
    "35": [
        "init_and_required_contract::test_init_writes_the_journal_rule_into_gitignore",
        "init_and_required_contract::test_init_appends_to_an_existing_gitignore_without_losing_it",
        "init_and_required_contract::test_init_is_idempotent_about_gitignore",
        "journal_v3::test_the_journal_is_ignored_in_the_plugin_tree",
    ],
    "36": [
        "journal_v3::test_without_a_journal_the_run_is_in_phase_1",
        "journal_v3::test_without_a_journal_the_task_file_is_still_writable",
    ],
    "37": [
        "contract_v1::test_the_unfilled_template_is_rejected_with_the_field_named",
        "contract_v1::test_doctor_refuses_a_contract_missing_one_of_the_four_points",
        "contract_v1::test_doctor_without_any_contract_fails_closed",
        "contract_v1::test_a_template_shaped_contract_leaves_the_gate_active",
        "deploy_v3::test_gate_discovers_the_ledger_from_the_contract",
        "deploy_v3::test_gate_refuses_loudly_when_the_contract_is_absent",
        "deploy_v3::test_gate_prefers_the_newest_ledger",
        "fail_loud::test_a_contract_without_a_ledger_field_is_refused_loudly",
        "fail_loud::test_a_contract_whose_pattern_matches_nothing_is_refused_loudly",
        "fail_loud::test_a_missing_ledger_can_still_be_created",
        "fail_loud::test_no_contract_at_all_is_refused",
        "fail_loud::test_absent_and_incomplete_contracts_are_both_refused_with_their_own_advice",
        "gate::test_the_gate_reads_only_the_project_contract",
        "gate::test_no_ledger_refuses_and_says_how_to_start",
        "gate::test_missing_test_paths_denies",
        "gate::test_missing_explicit_ledger_denies",
        "gate::test_no_ledger_in_payload_is_refused_for_the_contract",
        "harness::test_missing_ledger_fails_closed",
        "init_and_required_contract::test_init_leaves_unprovable_values_as_visible_placeholders",
        "init_and_required_contract::test_init_says_so_when_no_search_server_is_configured",
        "init_and_required_contract::test_init_warns_when_the_ledger_pattern_matches_nothing",
        "init_and_required_contract::test_gate_refuses_work_until_the_contract_is_verified",
        "init_and_required_contract::test_verifying_the_contract_opens_the_gate",
        "init_and_required_contract::test_a_missing_marker_counts_as_unverified",
        "init_and_required_contract::test_gate_refuses_work_in_a_project_with_no_suite",
        "init_and_required_contract::test_the_gate_refuses_a_project_with_no_contract",
        "init_and_required_contract::test_the_gate_refuses_a_contract_with_placeholders",
        "init_and_required_contract::test_a_completed_contract_lets_the_gate_work_normally",
        "init_and_required_contract::test_creating_the_contract_itself_is_never_refused",
    ],
    "38": [
        "init_and_required_contract::test_init_writes_a_contract_the_schema_recognises",
        "init_and_required_contract::test_init_fills_in_what_it_can_prove",
        "init_and_required_contract::test_init_fills_search_with_the_tools_cursor_actually_provides",
        "init_and_required_contract::test_init_keeps_the_builtin_search_when_mcp_lists_one",
        "init_and_required_contract::test_init_reports_which_fields_the_agent_must_confirm",
        "init_and_required_contract::test_init_finds_the_test_directory_whatever_it_is_called",
        "init_and_required_contract::test_a_directory_with_no_tests_is_not_a_test_path",
        "init_and_required_contract::test_init_finds_the_ledger_directory_whatever_it_is_called",
        "init_and_required_contract::test_init_detects_a_test_command_in_a_subdirectory",
        "init_and_required_contract::test_init_ignores_manifests_inside_vendor_directories",
        "init_and_required_contract::test_generated_contract_states_it_is_unverified",
        "init_and_required_contract::test_doctor_warns_that_an_unverified_contract_has_not_been_checked",
        "init_and_required_contract::test_init_fills_prohibitions_with_the_safe_baseline",
        "init_and_required_contract::test_init_detects_the_test_command_from_the_project",
        "init_and_required_contract::test_a_detected_contract_is_usable_after_one_confirmation",
        "init_and_required_contract::test_init_refuses_to_overwrite_an_existing_contract",
        "init_and_required_contract::test_init_can_force_after_an_explicit_request",
        "init_and_required_contract::test_a_freshly_initialised_contract_is_not_yet_usable",
        "init_and_required_contract::test_a_project_install_writes_a_starting_contract",
        "init_and_required_contract::test_a_project_install_does_not_overwrite_a_hand_written_contract",
        "write_scope_v3::test_a_weak_directory_name_needs_test_files_inside",
        "write_scope_v3::test_a_strong_directory_name_is_accepted_without_files",
    ],
    "39": [
        "contract_v1::test_the_required_points_are_the_four_of_the_spec",
        "contract_v1::test_test_paths_is_a_detail_of_the_full_suite_point",
        "contract_v1::test_every_required_field_maps_to_one_of_the_four_points",
        "contract_v1::test_raw_dumps_is_optional",
        "contract_v1::test_the_template_block_filled_in_passes_the_validator",
        "contract_v1::test_every_field_the_validator_requires_is_taught_by_the_template",
        "contract_v1::test_a_contract_without_test_paths_names_the_point_it_belongs_to",
        "contract_v1::test_raw_dumps_absence_is_a_warning_not_a_failure",
        "contract_v1::test_doctor_accepts_a_template_shaped_contract",
        "contract_v1::test_doctor_warns_about_raw_dumps_but_exits_zero",
        "contract_v1::test_doctor_reports_where_the_gate_would_look",
        "init_and_required_contract::test_doctor_warns_when_the_contract_is_older_than_the_schema",
        "init_and_required_contract::test_doctor_warns_when_the_contract_is_newer_than_the_schema",
        "init_and_required_contract::test_no_suite_is_a_problem_not_a_warning",
        "init_and_required_contract::test_init_documents_how_to_migrate_an_old_contract",
        "init_and_required_contract::test_the_doctor_version_warning_points_at_the_documented_procedure",
        "skill::test_contract_template_covers_every_required_field",
        "write_scope_v3::test_the_new_fields_do_not_add_a_fifth_required_point",
        "write_scope_v3::test_init_writes_the_three_new_fields",
    ],
    "40": [
        "deploy_v3::test_installer_writes_a_project_hook_per_harness",
        "deploy_v3::test_cursor_and_claude_templates_use_their_documented_project_variable",
        "gate::test_codex_template_uses_pretooluse",
        "gate::test_ts_adapters_use_the_right_api",
        "session_start_v3::test_cursor_answers_with_additional_context",
        "session_start_v3::test_claude_nests_the_context_and_names_the_event",
        "session_start_v3::test_codex_takes_the_flat_field",
        "session_start_v3::test_openclaw_blocks_with_a_non_empty_reason",
        "session_start_v3::test_openclaw_refuses_to_block_without_a_reason",
        "session_start_v3::test_opencode_refuses_rather_than_pretending",
        "session_start_v3::test_every_harness_has_an_adapter_file",
        "session_start_v3::test_a_two_key_response_is_refused_by_the_guard",
        "session_start_v3::test_the_entry_emits_cursor_shape_in_a_project_without_a_contract",
        "session_start_v3::test_each_harness_gets_its_own_shape_from_the_entry",
        "session_start_v3::test_opencode_says_nothing_and_explains_why",
        "session_start_v3::test_an_unknown_harness_is_a_wiring_error",
        "session_start_v3::test_the_adapter_is_chosen_by_the_harness_not_by_the_environment",
        "skill::test_harnesses_without_a_context_channel_do_not_pretend_to_have_one",
        "skill::test_invocation_table_covers_every_harness",
        "skill::test_invocation_syntax_matches_the_matrix",
        "skill::test_no_harness_specific_tool_name_is_used_as_an_instruction",
    ],
    "41": [
        "session_start_v3::test_a_ready_project_produces_no_message",
        "session_start_v3::test_no_contract_is_named_as_the_reason",
        "session_start_v3::test_an_unverified_contract_is_a_different_reason_from_a_missing_one",
        "session_start_v3::test_a_project_without_a_suite_is_named",
        "session_start_v3::test_a_contract_with_no_ledger_file_is_named",
        "session_start_v3::test_doctor_and_the_hook_share_one_implementation",
        "session_start_v3::test_the_entry_stays_silent_for_a_ready_project",
        "session_start_v3::test_cursor_rule_file_is_written_with_always_apply",
        "session_start_v3::test_agents_block_is_appended_to_an_existing_file",
        "session_start_v3::test_reinstalling_the_rule_does_not_duplicate_it",
        "session_start_v3::test_an_updated_rule_replaces_only_its_own_block",
        "session_start_v3::test_removal_takes_the_block_and_leaves_the_project_file",
        "session_start_v3::test_removal_deletes_a_file_that_held_only_the_block",
        "session_start_v3::test_claude_import_is_added_once",
        "session_start_v3::test_the_rule_names_no_project_specific_value",
        "session_start_v3::test_install_writes_the_cursor_rule",
        "session_start_v3::test_install_writes_agents_md_for_codex",
        "session_start_v3::test_install_gives_claude_the_import_not_a_second_copy",
        "session_start_v3::test_the_rule_mapping_exists_in_one_place",
        "session_start_v3::test_init_places_the_rule_so_a_plugin_only_install_is_initialised",
        "session_start_v3::test_init_twice_does_not_duplicate_the_rule",
        "skill::test_session_start_is_wired_where_the_harness_accepts_it",
        "skill::test_session_start_is_best_effort_and_says_so_where_it_is_read",
        "skill::test_the_reversal_is_recorded_where_a_future_reader_will_find_it",
    ],
    "42": [
        "cursor_payloads_v3::test_a_cursor_payload_yields_the_project_directory",
        "skill::test_skill_is_not_pinned_to_one_repository",
        "skill::test_the_plugin_tree_carries_no_host_project_contract",
    ],
    "43": [
        "project_boundary_v3::test_a_write_into_a_system_directory_is_refused",
        "project_boundary_v3::test_the_boundary_is_not_a_phase_rule",
        "project_boundary_v3::test_the_refusal_says_where_temporary_files_go",
        "project_boundary_v3::test_a_write_inside_the_project_is_judged_by_the_phase_rule",
        "project_boundary_v3::test_a_test_write_inside_the_project_is_still_allowed",
        "project_boundary_v3::test_an_absolute_path_inside_the_project_is_inside",
        "project_boundary_v3::test_a_shell_redirection_outside_the_project_is_refused",
        "project_boundary_v3::test_the_out_of_project_refusal_asks_the_user",
        "skill::test_no_forbidden_research_or_privilege_instructions",
    ],
    "44": [
        "project_boundary_v3::test_the_archivers_and_copy_tools_are_classified_correctly",
        "project_boundary_v3::test_rsync_out_of_the_project_is_refused_end_to_end",
        "project_boundary_v3::test_rsync_inside_the_project_is_still_a_phase_matter",
    ],
    "45": [
        "write_scope_v3::test_a_log_is_writable_in_every_phase",
        "write_scope_v3::test_the_raw_dump_directory_from_the_contract_is_writable",
        "write_scope_v3::test_a_log_write_with_no_readable_target_is_not_a_dump",
    ],
    "46": [
        "write_scope_v3::test_prose_and_data_are_writable_in_phase_one",
        "write_scope_v3::test_behaviour_files_are_still_refused_in_phase_one",
        "write_scope_v3::test_docs_paths_reads_a_list_and_tolerates_absence",
        "write_scope_v3::test_docs_paths_detection_offers_the_root_when_markdown_is_there",
        "write_scope_v3::test_a_doc_write_with_no_readable_target_is_not_documentation",
    ],
    "47": [
        "cli_v3::test_gate_accepts_the_explicit_ledger_and_dev_plugin_flags",
        "cli_v3::test_the_wrapper_forwards_gate_flags",
        "cli_v3::test_dev_plugin_allows_a_shell_write_inside_the_plugin_tree",
        "cli_v3::test_dev_plugin_still_refuses_a_shell_write_to_project_code",
        "cli_v3::test_without_the_flag_a_shell_write_to_the_plugin_is_refused",
        "write_scope_v3::test_editing_the_plugin_tree_is_refused_without_the_flag",
        "write_scope_v3::test_the_dev_plugin_flag_allows_the_plugin_tree",
        "write_scope_v3::test_the_flag_does_not_open_the_projects_code",
        "write_scope_v3::test_the_flag_is_only_a_cli_argument",
    ],
    "48": [
        "deploy_v3::test_installer_preserves_foreign_hooks",
        "deploy_v3::test_installer_is_idempotent_for_the_hook",
        "init_and_required_contract::test_writing_the_marker_itself_is_never_refused",
        "init_and_required_contract::test_installing_the_plugin_is_still_possible_without_a_contract",
        "session_start_v3::test_uninstall_removes_the_rule",
        "session_start_v3::test_uninstall_keeps_the_project_agents_md_text",
        "session_start_v3::test_uninstall_removes_every_hook_entry_not_just_the_gate",
        "session_start_v3::test_installing_twice_does_not_duplicate_the_status_hook",
        "session_start_v3::test_foreign_hooks_survive_both_the_install_and_the_removal",
        "write_scope_v3::test_the_installers_files_are_writable_in_phase_one",
    ],
    "49": [
        "changed_coverage_v3::test_a_python_file_without_a_coverage_record_counts_as_missed",
        "changed_coverage_v3::test_a_file_coverage_cannot_measure_is_left_out_of_the_denominator",
        "changed_coverage_v3::test_a_missing_python_file_is_not_excused_by_its_suffix",
        "write_scope_v3::test_the_changelog_is_refused_before_a_verdict",
        "write_scope_v3::test_the_changelog_is_refused_without_a_coverage_number",
        "write_scope_v3::test_the_changelog_is_refused_below_the_threshold",
        "write_scope_v3::test_the_changelog_is_allowed_at_the_threshold",
        "write_scope_v3::test_the_changelog_needs_a_way_to_verify_the_number",
        "write_scope_v3::test_min_coverage_defaults_to_the_spec_number",
        "write_scope_v3::test_coverage_command_is_empty_when_the_project_names_none",
        "write_scope_v3::test_coverage_command_detection_reads_the_manifest",
        "write_scope_v3::test_a_changelog_edit_without_content_is_refused",
        "write_scope_v3::test_a_green_run_allows_the_entry",
        "write_scope_v3::test_a_partially_failing_run_is_not_green",
        "write_scope_v3::test_no_green_marker_at_all_is_refused",
        "write_scope_v3::test_a_regression_after_the_green_run_is_refused",
        "write_scope_v3::test_a_regression_before_the_green_run_does_not_block",
    ],
}

#: Invariants no test can hold, with the §12 reason. Only 30: counting three runs is a
#: requirement on the agent, and one marker cannot show how often it was written.
UNAUTOMATED: dict[str, str] = {
    "30": "§12: the gate reads the marker, not how many runs produced it — the count of repetitions is a requirement on the agent",
}

#: Tests that hold a TASK-085 or repository requirement rather than a §13 invariant. Named
#: by the requirement, so the gap is visible instead of silently unclassified.
SUPPORT: dict[str, list[str]] = {
    "DoD-4 (one entry point)": [
        "cli_v3::test_the_entry_point_offers_every_verb_a_run_calls",
        "cli_v3::test_the_verifier_is_reachable_and_still_reports_honestly",
        "cli_v3::test_gate_returns_the_same_verdict_as_the_gate_script",
        "cli_v3::test_a_broken_plugin_tree_is_a_clear_refusal_not_a_traceback",
        "cli_v3::test_a_project_that_is_not_a_directory_is_refused",
        "cli_v3::test_the_wrapper_still_passes_the_exit_code_through",
        "gate::test_cli_exit_codes_and_json",
    ],
    "DoD-4a (documentation names working commands)": [
        "cli_v3::test_the_documentation_carries_no_plugin_relative_script_path",
        "cli_v3::test_every_command_the_documentation_names_is_a_verb_of_the_entry_point",
        "cli_v3::test_the_documentation_names_the_gate_and_the_state_machine_through_the_cli",
    ],
    "DoD-4b (a refusal names a runnable path)": [
        "cli_v3::test_the_phase_one_refusal_names_a_command_that_exists",
    ],
    "DoD-5 (packaging: manifest, templates, installer layout)": [
        "deploy_v3::test_plugin_manifest_matches_the_documented_schema",
        "deploy_v3::test_plugin_hook_uses_a_relative_path",
        "deploy_v3::test_wrapper_is_executable_in_the_source_tree",
        "deploy_v3::test_installed_wrapper_keeps_its_exec_bit",
        "deploy_v3::test_no_absolute_paths_are_written_by_the_installer",
        "deploy_v3::test_verifier_reports_a_binary_outside_path",
        "gate::test_verify_script_runs_and_self_tests",
        "gate::test_verify_script_finds_the_gate_without_a_parent_layout",
        "gate::test_ts_adapters_do_not_depend_on_bun",
        "gate::test_shell_hook_templates_quote_every_placeholder",
        "session_start_v3::test_the_entry_names_no_interpreter_path",
        "skill::test_the_declared_version_is_the_same_in_every_manifest",
        "skill::test_installer_lives_inside_the_skill",
        "skill::test_dry_run_writes_nothing",
        "skill::test_installer_is_not_copied_into_itself",
        "skill::test_unknown_target_fails_closed",
        "skill::test_openclaw_requires_explicit_dest",
        "skill::test_all_is_project_scope_only",
        "skill::test_openclaw_with_dest_lands_under_skills",
        "skill::test_install_is_idempotent_and_prunes_stale",
        "skill::test_destination_file_conflict_fails_closed",
        "skill::test_the_plugin_tests_are_not_installed",
        "skill::test_installer_has_no_python_syntax_errors",
        "skill::test_skill_id_comes_from_frontmatter_not_the_directory",
        "skill::test_vcs_metadata_is_never_copied",
        "skill::test_project_targets_map_to_expected_paths",
        "skill::test_personal_targets_go_to_home",
        "skill::test_opencode_global_path_is_config_not_home",
        "skill::test_readme_documents_every_target",
    ],
    "DoD-8 (a change is invisible until the window reloads)": [
        "session_start_v3::test_every_install_and_removal_names_the_restart",
        "session_start_v3::test_the_restart_notice_covers_every_installed_harness",
        "session_start_v3::test_the_restart_notice_is_not_printed_for_an_unknown_target",
    ],
    "Б-3 / Б-4 (Cursor tool names and captured payloads)": [
        "cursor_payloads_v3::test_the_fixture_records_the_shapes_that_were_actually_delivered",
        "cursor_payloads_v3::test_an_mcp_tool_is_not_a_write",
        "cursor_payloads_v3::test_the_mcp_call_is_allowed_during_a_run",
        "cursor_payloads_v3::test_a_hyphenated_mcp_server_is_covered_by_the_prefix",
        "cursor_payloads_v3::test_silencing_a_command_is_not_a_write",
        "cursor_payloads_v3::test_tools_that_never_reach_the_hook_are_recorded_not_invented",
    ],
    "Б-6 (the emergency switch is documented)": [
        "emergency_switch_v3::test_the_switch_must_be_a_whole_line",
        "emergency_switch_v3::test_the_switch_is_case_insensitive",
        "emergency_switch_v3::test_the_documentation_explains_the_switch_and_its_two_ways_out",
    ],
    "runner (§7 report, prompts, dry run, resume)": [
        "harness::test_repeated_failures_without_progress_stop_early",
        "harness::test_every_pass_is_recorded",
        "harness::test_dry_run_executes_nothing",
        "harness::test_resume_skips_recorded_passes",
        "harness::test_cursor_adapter_requires_a_key",
        "harness::test_agent_cmd_without_placeholder_is_refused",
        "harness::test_phase_prompt_matches_the_observed_phase",
        "harness::test_probe_prompt_names_the_markers_phase_four_must_write",
        "harness::test_every_phase_has_a_prompt",
    ],
    "§0 (fail closed: unclear is not 'no')": [
        "gate::test_phase1_allows_reads",
        "gate::test_verify_matrix_never_reports_an_unobserved_pass",
        "gate::test_unknown_tool_is_denied_not_allowed",
        "gate::test_known_read_only_tools_stay_allowed",
        "gate::test_shell_tool_without_a_readable_command_is_denied",
        "gate::test_shell_command_under_an_alternate_key_is_read",
        "gate::test_invalid_stdin_json_denies_instead_of_allowing",
        "gate::test_cli_empty_stdin_does_not_crash",
        "gate::test_cursor_template_is_fail_closed",
        "gate::test_claude_template_blocks_on_failure",
        "gate::test_ts_adapters_deny_on_spawn_failure",
        "gate::test_cursor_template_does_not_narrow_the_matcher",
        "gate::test_adapters_document_the_subagent_gap",
        "deploy_v3::test_gate_refuses_an_ambiguous_match_with_both_names",
        "deploy_v3::test_verifier_never_reports_an_unobserved_pass",
        "fail_loud::test_two_equally_new_ledgers_are_refused_with_the_candidates_named",
        "fail_loud::test_unreadable_input_denies",
        "fail_loud::test_a_refusal_always_carries_a_recommendation",
        "harness::test_missing_test_command_fails_closed",
        "init_and_required_contract::test_no_suite_refusal_offers_three_concrete_ways_forward",
        "init_and_required_contract::test_a_refused_run_records_the_problem_for_the_user",
        "skill::test_skill_has_no_silent_fallbacks_in_its_own_instructions",
        "skill::test_search_fallback_is_a_normal_mode_not_a_failure",
    ],
    "§15 (sudo is never run by the agent)": [
        "harness::test_sudo_in_the_test_command_is_refused",
        "harness::test_sudo_in_the_agent_command_is_refused",
    ],
    "repo rule 5.4 (no inline Python in Bash)": [
        "session_start_v3::test_the_entry_contains_no_inline_python",
        "skill::test_wrapper_contains_no_inline_python",
    ],
    "repo rule 5.9 (no world access)": [
        "session_start_v3::test_created_files_carry_no_world_access",
        "session_start_v3::test_a_projects_own_file_mode_is_left_alone",
        "session_start_v3::test_the_generated_contract_carries_no_world_access",
        "session_start_v3::test_the_mode_constants_are_declared_in_one_place",
        "session_start_v3::test_an_existing_gitignore_keeps_its_mode",
        "skill::test_installed_permissions_grant_no_world_access",
    ],
    "§9 (the DONE criteria and terminal statuses)": [
        "harness::test_agent_claiming_success_with_a_red_suite_is_blocked",
        "harness::test_green_suite_ends_done_when_a_results_table_exists",
        "harness::test_green_suite_without_a_results_table_is_not_done",
        "skill::test_success_criterion_is_four_parts",
    ],
    "skill structure (referenced files exist, frontmatter, README)": [
        "skill::test_skill_directory_is_complete",
        "skill::test_frontmatter_is_valid",
        "skill::test_referenced_paths_resolve",
        "skill::test_readme_describes_the_automaton_the_code_implements",
        "skill::test_the_readme_tree_lists_every_shipped_reference",
        "secret_wordings_v3::test_every_file_the_skill_points_at_still_exists",
        "secret_wordings_v3::test_the_forbidden_forms_are_named_beside_the_allowed_ones",
    ],
    "DoD-13 (the invariant matrix is complete in both directions)": [
        "invariant_matrix_v3::test_the_matrix_holds_all_fifty_four_invariants",
        "invariant_matrix_v3::test_every_invariant_names_a_test_or_says_why_it_cannot",
        "invariant_matrix_v3::test_an_unautomated_invariant_carries_a_reason",
        "invariant_matrix_v3::test_every_named_test_exists",
        "invariant_matrix_v3::test_every_test_in_the_suite_is_named",
        "invariant_matrix_v3::test_no_test_is_named_twice",
        "invariant_matrix_v3::test_the_matrix_says_what_it_is_for",
    ],
}


def spec_rows() -> dict[str, str]:
    """§13 as an id -> text mapping.

    Returns:
        Invariant text keyed by invariant id, in spec order.
    """
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("\n## 13. Инварианты")
    end = text.index("\n## 14. Канонический леджер")
    rows: dict[str, str] = {}
    for line in text[start:end].splitlines():
        match = re.match(r"\| (\d+[a-z]?) \| (.+) \|$", line)
        if match:
            rows[match.group(1)] = match.group(2).strip()
    return rows


def suite() -> dict[str, str]:
    """Every test function in the plugin's suite, keyed by ``stem::name``.

    Returns:
        Fully qualified node name mapped to its module stem.
    """
    found: dict[str, str] = {}
    for path in sorted(TESTS.glob("test_bugfixer_*.py")):
        stem = path.name[len("test_bugfixer_") : -len(".py")]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                found[f"{stem}::{node.name}"] = path.name
    return found


def main() -> int:
    """Validate the mapping and write the matrix.

    Returns:
        Process exit code: 0 when the matrix was written, 1 when it is incomplete.
    """
    rows = spec_rows()
    tests = suite()
    problems: list[str] = []

    unknown_ids = sorted((set(MAP) | set(UNAUTOMATED)) - set(rows))
    problems += [f"unknown invariant id: {i}" for i in unknown_ids]
    missing = [i for i in rows if i not in MAP and i not in UNAUTOMATED]
    problems += [f"invariant with no test and no reason: {i}" for i in missing]

    claimed: dict[str, list[str]] = {}
    for inv, nodes in MAP.items():
        for node in nodes:
            if node not in tests:
                problems.append(f"invariant {inv}: no such test {node}")
            claimed.setdefault(node, []).append(inv)
    for requirement, nodes in SUPPORT.items():
        for node in nodes:
            if node not in tests:
                problems.append(f"support {requirement}: no such test {node}")
            claimed.setdefault(node, []).append(requirement)

    unclaimed = sorted(set(tests) - set(claimed))
    problems += [f"test named by nothing: {node} ({tests[node]})" for node in unclaimed]

    duplicated = {node: where for node, where in claimed.items() if len(where) > 1}
    for node, where in sorted(duplicated.items()):
        problems.append(f"test named twice: {node} in {where}")

    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} problem(s); matrix not written")
        return 1

    matrix = {
        "about": (
            "Invariant -> test matrix for spec §13, DoD-13 of TASK-085. Generated from "
            "docs/bug-fixer-v3-spec.md; text is the spec's own wording, guarded against drift "
            "by tests/test_bugfixer_spec_fixture.py in the parent repository."
        ),
        "invariants": {
            inv: {
                "text": rows[inv],
                **({"tests": MAP[inv]} if MAP.get(inv) else {}),
                **({"unautomated": UNAUTOMATED[inv]} if inv in UNAUTOMATED else {}),
            }
            for inv in rows
        },
        "support": {
            requirement: {"tests": nodes} for requirement, nodes in SUPPORT.items()
        },
    }
    out = PLUGIN / "references" / "invariant-matrix.json"
    out.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(PLUGIN)}: {len(rows)} invariants, {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
