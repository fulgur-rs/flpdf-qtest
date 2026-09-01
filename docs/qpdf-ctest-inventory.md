# qpdf-ctest portable-behavior inventory

This inventory maps the qpdf 11.9.0 `qpdf-ctest.c` call sites that are
represented by the parity manifest. The C/C++ ABI itself is not a Rust parity
target. A `represented` row points at an existing Rust oracle for the
portable PDF behavior behind the C call; an `excluded` row points at the
ABI-only scope below.

## Counting boundary

The issue's 53 invocations are the qpdf-ctest calls in the manifest scope,
excluding the 120-row `content-preservation.test` test01 loop already closed
by `flpdf-25kg.2.7`. The current manifest has 72 provisional rows because it
also records dependent output/check rows. The qtest source currently expands
the encryption case table and the C-check case table through loops; those
expansions are counted as individual invocations here.

The authoritative qtest identity remains `<category> <ordinal>`. The
manifest, not this prose, is the machine-readable ledger.

## Portable behavior represented by Rust tests

| Manifest rows | qpdf call / responsibility | Rust oracle replacement |
|---|---|---|
| `c-api 1-2` | `test02`: read, static-ID write, and successful output lifecycle (`qpdf-ctest.c:161-171`) | `rust-test:flpdf-qtest-tools:qpdf_ctest_cli:qpdf_ctest_2_writes_output_and_completes_on_successful_authentication` |
| `c-api 3-4` | `test03`: content normalization (`qpdf-ctest.c:173-183`) | `rust-test:flpdf-cli:cli_tests:top_level_normalize_content_y_routes_to_content_normalizer` |
| `c-api 5-6` | `test04`: ignore xref streams (`qpdf-ctest.c:184-194`) | `rust-test:flpdf-cli:cli_tests:check_accepts_ignore_xref_streams_on_a_clean_pdf` |
| `c-api 7-8` | `test05`: linearized writer (`qpdf-ctest.c:195-210`) | `rust-test:flpdf-cli:cli_linearize:rewrite_linearize_then_check_passes` |
| `c-api 9-10` | `test06`: generated object streams (`qpdf-ctest.c:211-225`) | `rust-test:flpdf-cli:cli_qdf:qdf_object_streams_generate_matches_qpdf` |
| `c-api 11-12` | `test07`: QDF writer (`qpdf-ctest.c:226-236`) | `rust-test:flpdf-cli:cli_qdf:rewrite_qdf_produces_canonical_qdf` |
| `c-api 13-14` | `test08`: suppress original object IDs (`qpdf-ctest.c:237-248`) | `rust-test:flpdf-cli:cli_tests:rewrite_no_original_object_ids_is_accepted` |
| `c-api 15-16` | `test09`: uncompress stream-data policy (`qpdf-ctest.c:249-259`) | `rust-test:flpdf-cli:compat_matrix_baseline:compat_matrix_baseline` |
| `c-api 20-21` | `test41`: empty-PDF creation and static-ID output (`qpdf-ctest.c:1241-1251`) | `rust-test:flpdf:empty_pdf_tests:empty_document_write_matches_live_qpdf_static_id_empty` |
| `c-api-object-handle 1-2` | `test24`: portable object inspection/mutation behind C handles (`qpdf-ctest.c:507-656`) | `rust-test:flpdf:object_handle_parity_tests:dictionary_handle_lookup_and_writer_use_one_canonical_slash` |
| `c-api-object-handle 3-4` | `test25`: parsed object values (`qpdf-ctest.c:657-798`) | `rust-test:flpdf:object_handle_parity_tests:real_literal_round_trips_through_native_parsing` |
| `c-api-page 1-2` | `test34`: page lookup/add/remove and output (`qpdf-ctest.c:1012-1052`) | `rust-test:flpdf-cli:cli_tests:pages_cross_document_merge_is_supported` |
| `c-api-page 4` | `test36`: inherited page attributes (`qpdf-ctest.c:1099-1120`) | `rust-test:flpdf:cmp_linearize_tests:inherited_rotate_one_page_byte_identical_to_qpdf` |
| `c-api-page 5` | `test37`: page-cache refresh (`qpdf-ctest.c:1121-1137`) | `rust-test:flpdf:inspection_tests:page_refs_returns_pages_in_document_order` |
| `c-api-stream 1,3` | `test38`: raw/filtered page stream data and stream-boundary errors (`qpdf-ctest.c:1138-1188`) | `rust-test:flpdf:object_handle_page_content_pipeline_tests:pipe_page_contents_uses_qpdf_specialized_filter_decoding` |
| `c-api-stream 2` | `test39`: foreign-object copy (`qpdf-ctest.c:1189-1213`) | `rust-test:flpdf:copy_foreign_object_route_tests:public_copy_foreign_object_preserves_shared_child_identity` |
| `c-api-stream 4-5` | `test40`: new stream and writer-visible replacement (`qpdf-ctest.c:1214-1240`) | `rust-test:flpdf:object_handle_content_parser_tests:filter_page_contents_uses_the_canonical_pipeline_and_eof_lifecycle` |
| `error-condition 44` | `test10`: no-recovery policy and warning boundary (`qpdf-ctest.c:260-267`) | `rust-test:flpdf-cli:cli_tests:suppress_recovery_matches_qpdf_on_a_recoverable_xref_error` |
| `error-condition 90` | `test01` warning-bearing metadata read (`qpdf-ctest.c:136-159`) | `rust-test:flpdf:pdf_logger_tests:warning_replays_initial_repair_diagnostics_once_in_original_order` |
| `newline-before-endstream 11` | `test22`: writer newline policy (`qpdf-ctest.c:470-482`) | `rust-test:flpdf-cli:cli_tests:rewrite_newline_before_endstream_y_accepted_and_produces_valid_output` |
| `preserve-unref 5` | `test21`: preserve unreachable objects (`qpdf-ctest.c:458-469`) | `rust-test:flpdf-cli:cli_tests:preserve_unreferenced_retains_orphan_across_writer_cli_surfaces` |
| `qpdf-json 126,128` | `test42`/`test43`: JSON create from file/data (`qpdf-ctest.c:1252-1275`) | `rust-test:flpdf:json_document_tests:create_from_json_file_roundtrips_a_flpdf_authored_fixture_against_qpdf` |
| `qpdf-json 130,132` | `test44`/`test45`: JSON update from file/data (`qpdf-ctest.c:1276-1301`) | `rust-test:flpdf:json_document_tests:update_from_json_matches_qpdf_after_a_complete_flpdf_fixture_import` |
| `qpdf-json 134,136` | `test46`/`test47`: JSON output (`qpdf-ctest.c:1302-1320`) | `rust-test:flpdf:document_json_tests:write_json_matches_qpdf_json_output_bytes` |
| `writer-version 5` | `test14`: minimum/forced PDF version (`qpdf-ctest.c:313-327`) | `rust-test:flpdf-cli:cli_tests:rewrite_valid_min_version_succeeds` |
| `writer-version 6` | `test01`: version observation after the C writer path | `rust-test:flpdf-qtest-tools:qpdf_ctest_cli:qpdf_ctest_1_reports_linearized_metadata` |
| `writer-version 7` | forced-version output check | `rust-test:flpdf-cli:cli_tests:rewrite_valid_force_version_succeeds` |
| `encryption 239,241,243,245,247,249,251,253,255,257` | `test02`, `test11`-`test18`: portable encryption writer/readback (`qpdf-ctest.c:161-170,268-434`; qtest `encryption.test:356-423`) | `rust-test:flpdf-qtest-tools:qpdf_ctest_cli:qpdf_ctest_encryption_writer_cases_cover_r2_through_r6` |

## ABI-only scope

The following rows assert C handle identity, callback ABI, C error-object
ownership, or C string-buffer lifetime rather than an independent portable PDF
behavior. They remain `excluded` and use the scope reference below:

`c-api 17-19`, `c-api-check 1-2`, `c-api-object-handle 5-13`, `c-api-page 3`,
`error-condition 42-43`, and `specific-file 2`.

`c-api-object-handle 5-6` also exercises qpdf's direct-to-indirect promotion;
the Rust semantic decision remains tracked separately by
`flpdf-25kg.2.6`, while this direct C-handle assertion stays outside the ABI
parity denominator.

Scope reference: `scope:docs/qpdf-ctest-inventory.md#abi-only`.
