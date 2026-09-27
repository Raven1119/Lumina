# 对话记忆 v1 实施结果

## 清理前依赖盘点

下表以改动前的本地 HEAD 为依据扫描跟踪文件；清理时发现的额外文件也回查该 HEAD 后补入。导入列记录显式 Python import；测试与文档列记录路径或模块名引用。旧二进制与 JSON 产物没有可解析的 import。总计 701 行，删除内容仍由本地归档标签保存。

| 文件 | 处置 | 导入者 | 测试引用 | 文档引用 | 理由 |
| --- | --- | --- | --- | --- | --- |
| `.env.example` | 改写 | — | — | `README.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `.gitmodules` | 删除 | — | — | `docs/TASK_memory_v1.md` | 删除失效路径 |
| `AGENTS.md` | 改写 | — | — | `Dream/AGENTS.md`<br>`Memory_lab/docs/TASK_CARD.md`<br>`docs/EXPERIMENTS.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`<br>`docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md`<br>`docs/TASK_memory_v1.md`<br>`docs/plan/CODEX_INTENTION_NERVOUS_STAGE1.md`<br>`docs/plan/MIND_DEFINITION_V1.md` | 更新正式入口和事实 |
| `Conversation_Memory/AGENTS.md` | 改写 | — | — | `AGENTS.md`<br>`Dream/AGENTS.md`<br>`docs/TASK_memory_v1.md` | 新记忆器官契约 |
| `Conversation_Memory/MAGMA_COMMIT.txt` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/__init__.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_anchor_fusion.py` | 删除 | `Conversation_Memory/tests/test_entity_relation_recall.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`Conversation_Memory/tests/test_source_lexical.py` | `Conversation_Memory/tests/test_cold_draft_adapter.py` | `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_associative_recall.py` | 删除 | `Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_first_hit_recall.py`<br>`Conversation_Memory/tests/test_reliable_recall_dispatch.py`<br>`tests/test_body_memory_chat.py` | `Conversation_Memory/tests/test_calibrated_first_hit.py`<br>`Conversation_Memory/tests/test_reliable_recall.py`<br>`Conversation_Memory/tests/test_reliable_recall_v2.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_body_formation.py` | 删除 | `Conversation_Memory/tests/test_body_memory.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_body_recall.py` | 删除 | `Conversation_Memory/tests/test_body_memory.py`<br>`tests/test_body_memory_chat.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_calibrated_index.py` | 删除 | `Conversation_Memory/tests/test_calibrated_first_hit.py` | — | `Memory_lab/docs/TASK_CARD.md`<br>`README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_calibrated_recall.py` | 删除 | `Conversation_Memory/tests/test_calibrated_first_hit.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_entity_ingestion.py` | 删除 | — | `Conversation_Memory/tests/test_reliable_formation.py` | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_first_hit_ingestion.py` | 删除 | `Conversation_Memory/tests/test_first_hit_ingestion.py` | `Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py` | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_first_hit_read.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_graph_read.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_grounded_spans.py` | 删除 | `Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_grounded_spans.py`<br>`scripts/recall_e2e_test.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_query_graph_read.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_recall_execution.py` | 删除 | `Conversation_Memory/tests/test_entity_relation_recall.py` | `Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_entity_membership_recall.py` | `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_reliable_projection.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_reliable_recall.py` | 删除 | `Conversation_Memory/tests/test_query_graph_read.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_semantic_recall.py` | 删除 | `Conversation_Memory/tests/test_calibrated_first_hit.py` | `Conversation_Memory/tests/test_semantic_recall_v4.py`<br>`Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`tests/test_semantic_associative_v4_chat.py`<br>`tests/test_semantic_associative_v5_chat.py`<br>`tests/test_semantic_associative_v6_chat.py` | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_semantic_recall_v3.py` | 删除 | `Conversation_Memory/tests/test_calibrated_first_hit.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_semantic_recall_v4.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v4.py`<br>`tests/test_semantic_associative_v4_chat.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_semantic_recall_v5.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`tests/test_semantic_associative_v5_chat.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_semantic_recall_v6.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`tests/test_semantic_associative_v6_chat.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/_source_backend.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/backend.py` | 删除 | `Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_entity_conditioned_recall.py`<br>`Conversation_Memory/tests/test_entity_membership_recall.py`<br>`Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_entity_relation_recall.py`<br>`Conversation_Memory/tests/test_first_hit.py`<br>`Conversation_Memory/tests/test_ordinary_entity_write_binding.py`<br>`Conversation_Memory/tests/test_source_backend_views.py`<br>`Conversation_Memory/tests/test_source_range_backend.py`<br>`Conversation_Memory/tests/test_temporal_boundary.py`<br>`scripts/recall_e2e_test.py` | `Conversation_Memory/tests/entity_memory_acceptance.py`<br>`Conversation_Memory/tests/test_calibrated_first_hit.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py` | `README.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/body_payload.py` | 删除 | `Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`tests/test_body_memory_chat.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/calibrated_first_hit_parameters.json` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/controlled_relation.py` | 删除 | `Conversation_Memory/tests/test_controlled_relation.py`<br>`experiments/mind_relation.py`<br>`tests/test_mind_relation_shadow.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/entity_consolidation.py` | 删除 | `Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_memory_admission.py`<br>`Conversation_Memory/tests/test_ordinary_entity_write_binding.py` | `Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_reliable_formation.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/entity_mentions.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/first_hit.py` | 删除 | `Conversation_Memory/docs/BODY_MEMORY.md`<br>`Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Conversation_Memory/tests/test_calibrated_first_hit.py`<br>`Conversation_Memory/tests/test_first_hit.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_first_hit_read.py`<br>`Conversation_Memory/tests/test_first_hit_recall.py`<br>`Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_query_first_hit.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`Conversation_Memory/tests/test_reliable_recall_dispatch.py`<br>`tests/test_chat_api.py` | `Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_recall.py`<br>`Conversation_Memory/tests/test_reliable_recall_v2.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py`<br>`tests/test_body_memory_chat.py` | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/graph_read_query.py` | 删除 | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_graph_read_query.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`tests/test_query_mind_chat.py`<br>`tests/test_query_mind_gate.py` | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/grounded_formation.py` | 删除 | `Conversation_Memory/tests/test_entity_formation_v2.py`<br>`Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_grounded_formation.py`<br>`Conversation_Memory/tests/test_identity_coverage.py`<br>`Conversation_Memory/tests/test_memory_admission.py`<br>`Conversation_Memory/tests/test_memory_reliability_formation.py`<br>`Conversation_Memory/tests/test_memory_retryability.py`<br>`Conversation_Memory/tests/test_memory_source_coverage.py`<br>`Conversation_Memory/tests/test_ordinary_entity_write_binding.py`<br>`Conversation_Memory/tests/test_user_self_binding.py` | `Conversation_Memory/tests/test_reliable_profile_integration.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/identity_coverage.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/interfaces.py` | 删除 | `scripts/recall_e2e_test.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/magma_adapter.py` | 删除 | `Conversation_Memory/docs/BODY_MEMORY.md`<br>`Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`Conversation_Memory/tests/test_association_selection.py`<br>`Conversation_Memory/tests/test_bge_reranker.py`<br>`Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_controlled_relation.py`<br>`Conversation_Memory/tests/test_entity_conditioned_recall.py`<br>`Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_entity_membership_recall.py`<br>`Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_first_hit_recall.py`<br>`Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_grounded_formation.py`<br>`Conversation_Memory/tests/test_identity_coverage.py`<br>`Conversation_Memory/tests/test_memory_read_foundation.py`<br>`Conversation_Memory/tests/test_ordinary_entity_write_binding.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`Conversation_Memory/tests/test_reliable_recall_dispatch.py`<br>`Conversation_Memory/tests/test_semantic_recall_v4.py`<br>`Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`Conversation_Memory/tests/test_semantic_recall_v7.py`<br>`Conversation_Memory/tests/test_source_context_rendering.py`<br>`Conversation_Memory/tests/test_source_memory.py`<br>`Conversation_Memory/tests/test_user_self_binding.py`<br>`Dream/tests/test_dream_cold_draft_digest.py`<br>`experiments/mind_relation.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_chat_api.py` | `Conversation_Memory/tests/entity_memory_acceptance.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_recall_v2.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py`<br>`Dream/tests/test_dream_progress.py`<br>`tests/test_body_memory_chat.py`<br>`tests/test_calibrated_memory_chat.py`<br>`tests/test_query_mind_chat.py` | `README.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/models.py` | 删除 | `Conversation_Memory/docs/BODY_MEMORY.md`<br>`Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md`<br>`Conversation_Memory/ingestion/fixture_loader.py`<br>`Conversation_Memory/ingestion/temporal.py`<br>`Conversation_Memory/recall/rendering.py`<br>`Conversation_Memory/tests/test_association_selection.py`<br>`Conversation_Memory/tests/test_bge_reranker.py`<br>`Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Conversation_Memory/tests/test_calibrated_first_hit.py`<br>`Conversation_Memory/tests/test_chinese_temporal_parser.py`<br>`Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_controlled_relation.py`<br>`Conversation_Memory/tests/test_entity_conditioned_recall.py`<br>`Conversation_Memory/tests/test_entity_formation_v2.py`<br>`Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_entity_membership_recall.py`<br>`Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_entity_relation_recall.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_first_hit_recall.py`<br>`Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_graph_read_query.py`<br>`Conversation_Memory/tests/test_grounded_formation.py`<br>`Conversation_Memory/tests/test_grounded_spans.py`<br>`Conversation_Memory/tests/test_identity_coverage.py`<br>`Conversation_Memory/tests/test_ordinary_entity_write_binding.py`<br>`Conversation_Memory/tests/test_prepared_recall.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_recall_dispatch.py`<br>`Conversation_Memory/tests/test_semantic_recall_v4.py`<br>`Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`Conversation_Memory/tests/test_semantic_recall_v7.py`<br>`Conversation_Memory/tests/test_source_backend_views.py`<br>`Conversation_Memory/tests/test_source_context.py`<br>`Conversation_Memory/tests/test_source_context_rendering.py`<br>`Conversation_Memory/tests/test_source_experiences.py`<br>`Conversation_Memory/tests/test_source_lexical.py`<br>`Conversation_Memory/tests/test_source_memory.py`<br>`Conversation_Memory/tests/test_source_reader.py`<br>`Conversation_Memory/tests/test_user_self_binding.py`<br>`Dream/cold_draft_digest.py`<br>`Dream/tests/test_dream_cold_draft_digest.py`<br>`Dream/tests/test_dream_progress.py`<br>`experiments/mind_relation.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_body_memory_chat.py`<br>`tests/test_calibrated_memory_chat.py`<br>`tests/test_chat_api.py`<br>`tests/test_cold_draft_progress.py`<br>`tests/test_execution_api.py`<br>`tests/test_grounded_historical_claim_guard.py`<br>`tests/test_history_api.py`<br>`tests/test_memory_evidence_selection.py`<br>`tests/test_message_runtime.py`<br>`tests/test_mind_gate.py`<br>`tests/test_mind_promotion_controls.py`<br>`tests/test_query_mind_chat.py`<br>`tests/test_recall_e2e_script.py`<br>`tests/test_semantic_associative_chat.py`<br>`tests/test_semantic_associative_v2_chat.py`<br>`tests/test_semantic_associative_v3_chat.py` | `Conversation_Memory/tests/entity_memory_acceptance.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_recall.py`<br>`Conversation_Memory/tests/test_reliable_recall_v2.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py` | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/reliable_formation.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/semantic_protocol.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`Conversation_Memory/tests/test_semantic_recall_v7.py`<br>`Mind/evidence_selector.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/source_context.py` | 删除 | `Conversation_Memory/tests/test_source_context.py` | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/source_experiences.py` | 删除 | — | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/source_memory.py` | 删除 | `Conversation_Memory/tests/test_source_backend_views.py`<br>`Conversation_Memory/tests/test_source_context.py`<br>`Conversation_Memory/tests/test_source_experiences.py`<br>`Conversation_Memory/tests/test_source_lexical.py`<br>`Conversation_Memory/tests/test_source_memory.py`<br>`Conversation_Memory/tests/test_source_range_backend.py`<br>`Conversation_Memory/tests/test_source_reader.py` | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/source_reader.py` | 删除 | `Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md`<br>`Conversation_Memory/tests/test_source_dense_failure.py`<br>`Conversation_Memory/tests/test_source_experiences.py`<br>`Conversation_Memory/tests/test_source_reader.py` | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/adapter/user_self.py` | 删除 | `Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_identity_coverage.py`<br>`Conversation_Memory/tests/test_user_self_binding.py` | `Conversation_Memory/tests/test_association_selection.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/BODY_MEMORY.md` | 删除 | — | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/CHINESE_TEMPORAL_PARSER.md` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md` | 删除 | — | — | `docs/CURRENT_STATUS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/FIRST_HIT_MEMORY.md` | 删除 | — | — | `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`<br>`README.md`<br>`docs/CURRENT_STATUS.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/PROVENANCE_AND_IDEMPOTENCY.md` | 删除 | — | — | `AGENTS.md`<br>`Dream/AGENTS.md`<br>`Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`<br>`README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/RELIABLE_MEMORY.md` | 删除 | — | — | `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`<br>`README.md`<br>`docs/EXPERIMENTS.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md` | 删除 | — | — | `README.md`<br>`docs/CURRENT_STATUS.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/fixtures/cold_draft_segment_legacy.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/fixtures/cold_draft_segment_v1.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/fixtures/cold_draft_segment_v2.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/fixtures/exact_9_validator_rejection_audit_cases.json` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/fixtures/formation_validator_audit_cases.json` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/ingestion/__init__.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/ingestion/entities.py` | 删除 | `Conversation_Memory/adapter/magma_adapter.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/ingestion/fixture_loader.py` | 删除 | `Conversation_Memory/tests/test_cold_draft_adapter.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/ingestion/state_store.py` | 删除 | `Conversation_Memory/adapter/magma_adapter.py`<br>`Conversation_Memory/docs/BODY_MEMORY.md`<br>`Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`Conversation_Memory/tests/test_association_selection.py`<br>`Conversation_Memory/tests/test_bge_reranker.py`<br>`Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_controlled_relation.py`<br>`Conversation_Memory/tests/test_entity_conditioned_recall.py`<br>`Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_entity_membership_recall.py`<br>`Conversation_Memory/tests/test_entity_node_write_path.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_first_hit_recall.py`<br>`Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_grounded_formation.py`<br>`Conversation_Memory/tests/test_identity_coverage.py`<br>`Conversation_Memory/tests/test_ordinary_entity_write_binding.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`Conversation_Memory/tests/test_reliable_recall_dispatch.py`<br>`Conversation_Memory/tests/test_source_context_rendering.py`<br>`Conversation_Memory/tests/test_source_memory.py`<br>`Conversation_Memory/tests/test_user_self_binding.py`<br>`Dream/tests/test_dream_cold_draft_digest.py`<br>`experiments/mind_relation.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_chat_api.py` | `Conversation_Memory/tests/entity_memory_acceptance.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_recall_v2.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py`<br>`tests/test_body_memory_chat.py` | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/ingestion/temporal.py` | 删除 | `Conversation_Memory/adapter/magma_adapter.py`<br>`Conversation_Memory/tests/test_chinese_temporal_parser.py`<br>`Conversation_Memory/tests/test_cold_draft_adapter.py` | — | `Memory_lab/docs/TASK_CARD.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/recall/__init__.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/recall/bge_reranker.py` | 删除 | `Conversation_Memory/adapter/magma_adapter.py`<br>`Conversation_Memory/tests/test_bge_reranker.py`<br>`Conversation_Memory/tests/test_memory_read_foundation.py`<br>`Conversation_Memory/tests/test_source_experiences.py`<br>`scripts/recall_e2e_test.py` | `Conversation_Memory/tests/test_association_selection.py` | `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/recall/hindsight_scoring.py` | 删除 | `Conversation_Memory/adapter/magma_adapter.py`<br>`Conversation_Memory/adapter/source_context.py`<br>`Conversation_Memory/adapter/source_memory.py`<br>`Conversation_Memory/tests/test_hindsight_scoring.py`<br>`Conversation_Memory/tests/test_memory_read_foundation.py` | — | `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/recall/rendering.py` | 删除 | `Conversation_Memory/adapter/_associative_recall.py`<br>`Conversation_Memory/adapter/_body_recall.py`<br>`Conversation_Memory/adapter/_calibrated_recall.py`<br>`Conversation_Memory/adapter/_reliable_recall.py`<br>`Conversation_Memory/adapter/_semantic_recall.py`<br>`Conversation_Memory/adapter/_semantic_recall_v3.py`<br>`Conversation_Memory/adapter/_semantic_recall_v4.py`<br>`Conversation_Memory/adapter/_semantic_recall_v5.py`<br>`Conversation_Memory/adapter/_semantic_recall_v6.py`<br>`Conversation_Memory/adapter/magma_adapter.py`<br>`Conversation_Memory/tests/test_cold_draft_adapter.py`<br>`Conversation_Memory/tests/test_entity_relation_recall.py`<br>`Conversation_Memory/tests/test_source_context_rendering.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/requirements.txt` | 改写 | — | — | `README.md` | 新记忆器官契约 |
| `Conversation_Memory/tests/entity_memory_acceptance.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/fixtures/calibrated_associative_synthetic.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/fixtures/entity_memory_development_budget.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/fixtures/entity_memory_development_smoke.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/fixtures/entity_memory_holdout.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/fixtures/memory_reliability_holdout.json` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_association_selection.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_bge_reranker.py` | 删除 | `Conversation_Memory/tests/test_memory_read_foundation.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_body_memory.py` | 删除 | `Conversation_Memory/tests/test_body_memory_persistence.py`<br>`tests/test_body_memory_chat.py` | — | `Conversation_Memory/docs/BODY_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_body_memory_persistence.py` | 删除 | — | — | `Conversation_Memory/docs/BODY_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_calibrated_first_hit.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v4.py`<br>`Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`tests/test_semantic_associative_v4_chat.py` | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_chinese_temporal_parser.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_cold_draft_adapter.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_controlled_relation.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_entity_conditioned_recall.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_entity_formation_v2.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_entity_ingestion_v2.py` | 删除 | `Conversation_Memory/tests/test_entity_membership_recall.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_memory_admission.py`<br>`Conversation_Memory/tests/test_memory_retryability.py`<br>`Conversation_Memory/tests/test_memory_source_coverage.py` | `Conversation_Memory/tests/test_reliable_formation.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_entity_membership_recall.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_entity_node_write_path.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_entity_relation_recall.py` | 删除 | `Conversation_Memory/tests/test_entity_membership_recall.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_first_hit.py` | 删除 | — | `Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_query_first_hit.py`<br>`Conversation_Memory/tests/test_query_graph_read.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py` | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_first_hit_ingestion.py` | 删除 | `Conversation_Memory/tests/test_body_memory.py` | `Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_first_hit_read.py` | 删除 | `Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_query_first_hit.py`<br>`Conversation_Memory/tests/test_query_graph_read.py` | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_first_hit_recall.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_graph_read_facade.py` | 删除 | — | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_graph_read_query.py` | 删除 | — | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_grounded_formation.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_grounded_spans.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_hindsight_scoring.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_identity_coverage.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_memory_admission.py` | 删除 | `Conversation_Memory/tests/test_entity_membership_recall.py`<br>`Conversation_Memory/tests/test_memory_retryability.py`<br>`Conversation_Memory/tests/test_memory_source_coverage.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_memory_read_foundation.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_memory_reliability_acceptance.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_memory_reliability_formation.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_memory_retryability.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_memory_source_coverage.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_ordinary_entity_write_binding.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_prepared_recall.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_query_first_hit.py` | 删除 | — | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_query_graph_read.py` | 删除 | — | `tests/test_query_mind_chat.py` | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_formation.py` | 删除 | `Conversation_Memory/tests/test_body_memory.py`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_v6_ingestion.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_profile_integration.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_recall.py` | 删除 | — | `Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_query_graph_read.py` | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_recall_dispatch.py` | 删除 | `Conversation_Memory/tests/test_graph_read_facade.py`<br>`Conversation_Memory/tests/test_query_graph_read.py` | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_recall_v2.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_v5_ingestion.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_reliable_v6_ingestion.py` | 删除 | — | — | `README.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_semantic_recall_v4.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_semantic_recall_v5.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`Conversation_Memory/tests/test_semantic_recall_v7.py`<br>`tests/test_semantic_associative_v5_chat.py`<br>`tests/test_semantic_associative_v6_chat.py` | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_semantic_recall_v6.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_semantic_recall_v7.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_backend_views.py` | 删除 | `Conversation_Memory/tests/test_source_dense_failure.py`<br>`Conversation_Memory/tests/test_source_experiences.py`<br>`Conversation_Memory/tests/test_source_range_backend.py`<br>`Conversation_Memory/tests/test_source_reader.py` | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_context.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_context_rendering.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_dense_failure.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_experiences.py` | 删除 | `Conversation_Memory/tests/test_source_dense_failure.py` | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_lexical.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_memory.py` | 删除 | `Conversation_Memory/tests/test_source_context.py`<br>`Conversation_Memory/tests/test_source_experiences.py`<br>`Conversation_Memory/tests/test_source_reader.py` | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_range_backend.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_source_reader.py` | 删除 | `Conversation_Memory/tests/test_source_experiences.py` | — | `docs/EXPERIMENTS.md` | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_temporal_boundary.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/tests/test_user_self_binding.py` | 删除 | — | — | — | 旧 MAGMA 栈或其验证材料 |
| `Conversation_Memory/upstream/MAGMA` | 删除 | — | `Conversation_Memory/tests/entity_memory_acceptance.py` | `Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`<br>`Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`<br>`README.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`<br>`docs/RECALL_E2E_ACCEPTANCE.md` | 旧 MAGMA 栈或其验证材料 |
| `Dream/AGENTS.md` | 改写 | — | — | `AGENTS.md`<br>`docs/TASK_memory_v1.md` | 改为记忆 v1 Dream 入口 |
| `Dream/cold_draft_digest.py` | 删除 | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Dream/tests/test_dream_cold_draft_digest.py`<br>`Dream/tests/test_dream_progress.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_cold_draft_progress.py` | `Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_profile_integration.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`tests/test_cold_source_window.py` | `README.md`<br>`docs/TASK_memory_v1.md` | 旧同步消化流程 |
| `Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md` | 删除 | — | — | `AGENTS.md`<br>`Dream/AGENTS.md`<br>`README.md` | 旧同步消化流程 |
| `Dream/interfaces.py` | 删除 | — | — | — | 旧同步消化流程 |
| `Dream/models.py` | 删除 | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Dream/tests/test_dream_cold_draft_digest.py`<br>`Dream/tests/test_dream_progress.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_cold_draft_progress.py` | `Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_grounded_formation.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py`<br>`tests/test_body_memory_chat.py` | — | 旧同步消化流程 |
| `Dream/tests/test_dream_cold_draft_digest.py` | 删除 | — | — | — | 旧同步消化流程 |
| `Dream/tests/test_dream_progress.py` | 删除 | — | — | — | 旧同步消化流程 |
| `Lumina_Canvas/Lumina_MAGMA.canvas` | 删除 | — | — | `docs/TASK_memory_v1.md` | 删除失效路径 |
| `Lumina_Canvas/Lumina_Memory_Architecture.canvas` | 改写 | — | — | `docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `Memory_lab/AGENTS.md` | 改写 | — | — | `Memory_lab/docs/TASK_CARD.md`<br>`docs/TASK_memory_v1.md` | 保留历史原文或接正式器官 |
| `Memory_lab/README.md` | 改写 | — | — | `Memory_lab/docs/TASK_answer_v2.md` | 保留历史原文或接正式器官 |
| `Memory_lab/answers_v1/real_answer_dev_a_B1_v2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_a_B1_v2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_a_P6_v1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_a_P6_v1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_b_B1_v1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_b_B1_v1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_b_P6_v1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v1/real_answer_dev_b_P6_v1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_a_B1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_a_B1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_a_P6/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_a_P6/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_b_B1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_b_B1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_b_P6/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e1_dev_b_P6/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_a_B1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_a_B1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_a_P6/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_a_P6/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_b_B1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_b_B1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_b_P6/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e2_dev_b_P6/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e3_dev_a_P6r10/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e3_dev_a_P6r10/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e3_dev_b_P6r10/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/e3_dev_b_P6r10/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/pi_recall_sweep/comparison.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/pi_recall_sweep/comparison_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v2/pi_recall_sweep/comparison_v2.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_P6u/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_P6u/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_P6u10/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_P6u10/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_int2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_int2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_long8w_B1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_long8w_B1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_long8w_final/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_long8w_final/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_render2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_a_render2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_P6u/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_P6u/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_P6u10/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_P6u10/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_int2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_int2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_long8w_B1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_long8w_B1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_long8w_final/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_long8w_final/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_render2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/answer_dev_b_render2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_P6u_P6u10/comparison.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_P6u_P6u10/comparison_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_P6u_P6u10/comparison_v2.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_int1_int2/comparison.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_int1_int2/comparison_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_int1_int2/comparison_v2.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_long_B1_final/comparison.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_long_B1_final/comparison_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_long_B1_final/comparison_v2.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_render1_render2/comparison.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_render1_render2/comparison_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v3/suite_render1_render2/comparison_v2.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_a_P6u_noise1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_a_P6u_noise1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_a_P6u_noise1/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_a_P6u_noise2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_a_P6u_noise2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_a_P6u_noise2/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_b_P6u_noise1/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_b_P6u_noise1/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_b_P6u_noise1/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_b_P6u_noise2/config.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_b_P6u_noise2/probes.jsonl` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/answers_v4/answer_dev_b_P6u_noise2/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/docs/DESIGN.md` | 移至 history | — | — | `Memory_lab/docs/TASK_CARD.md` | 保留历史原文或接正式器官 |
| `Memory_lab/docs/DESIGN_v5.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/DESIGN_v5_1.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/DESIGN_v5_2.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_answer_v2.md` | 移至 history | — | — | `Memory_lab/docs/TASK_answer_v2.md` | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_v1.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_v3.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_v4.md` | 移至 history | — | — | `Memory_lab/docs/TASK_v4.md` | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_v5.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_v5_1.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/RESULTS_v5_2.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_CARD.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_answer_v2.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_measure_v2.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_v3.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_v4.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_v5.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_v5_1.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/docs/TASK_v5_2.md` | 移至 history | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/FINDINGS.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/judge_deepseek/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v1_B1_vs_P6/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_FINDINGS.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_e1_vs_e2/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_P6_v1_vs_e1/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e1_B1_vs_P6/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v2_e2_B1_vs_P6/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/judge_self_decisions.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6_vs_P6u/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/judge_self_decisions.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p1_P6u_vs_P6u10/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/judge_self_decisions.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p2_int1_vs_int2/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/judge_self_decisions.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p3_render1_vs_render2/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/direct_decisions.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/out_dev_a_long8w_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/out_dev_a_long8w_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/out_dev_b_long8w_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/out_dev_b_long8w_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_self/out_dev_a_long8w_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_self/out_dev_a_long8w_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_self/out_dev_b_long8w_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_self/out_dev_b_long8w_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_self/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/judge_self/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/key_dev_a_long8w.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/key_dev_b_long8w.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/packet_dev_a_long8w_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/packet_dev_a_long8w_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/packet_dev_b_long8w_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v3_p4_long_B1_vs_final/packet_dev_b_long8w_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_original_vs_seed1/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_noise_seed1_vs_seed2/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p1_P6u_vs_P6d/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p2_nocheck_vs_check/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/out_dev_a_long16w_v2_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/out_dev_a_long16w_v2_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/out_dev_b_long16w_v2_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/out_dev_b_long16w_v2_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/key_dev_a_long16w_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/key_dev_b_long16w_v2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/packet_dev_a_long16w_v2_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/packet_dev_a_long16w_v2_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/packet_dev_b_long16w_v2_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p3_long_B1_vs_final/packet_dev_b_long16w_v2_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/judge_deepseek_v4/out_holdout_c_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/judge_deepseek_v4/out_holdout_c_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/judge_deepseek_v4/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/judge_deepseek_v4/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/judge_deepseek_v4/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/key_holdout_c.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/packet_holdout_c_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v4_p4_holdout_B1_vs_final/packet_holdout_c_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/judge_flash/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_P7_vs_P8/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/judge_flash/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v51_answer_v4_vs_v5/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/judge_judge_flash/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v52_P8_vs_P9/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/out_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/out_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/out_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/out_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/run_status.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/summary.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/judge_flash/summary.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/key_dev_a.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/key_dev_b.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/meta.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/packet_dev_a_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/packet_dev_a_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/packet_dev_b_1.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/judge/rounds/v5_P6d_pro_vs_P7_flash/packet_dev_b_2.json` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/lab/answer.py` | 改写 | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/lab/replay.py` | 改写 | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/lab/usage_judge.py` | 改写 | — | — | — | 保留历史原文或接正式器官 |
| `Memory_lab/prompts/answer_v1.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/prompts/answer_v2.md` | 保留 | `Memory_lab/lab/answer.py` | — | `Memory_lab/docs/history/TASK_answer_v2.md` | B1 回答模式仍用 v2；原文逐字保留 |
| `Memory_lab/prompts/answer_v3.md` | 删除 | — | — | `Memory_lab/docs/TASK_answer_v2.md` | 历史实验产物/旧预设 |
| `Memory_lab/prompts/answer_v4.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/prompts/repair_v1.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/prompts/usage_v1.md` | 删除 | — | — | — | 历史实验产物/旧预设 |
| `Memory_lab/requirements.txt` | 改写 | — | — | `Memory_lab/docs/TASK_CARD.md` | 保留历史原文或接正式器官 |
| `Mind/AGENTS.md` | 改写 | — | — | `AGENTS.md`<br>`docs/TASK_memory_v1.md` | 去除旧门控说明 |
| `Mind/constant_gate.py` | 删除 | `scripts/mind_gate_shadow.py`<br>`tests/test_body_memory_chat.py`<br>`tests/test_calibrated_memory_chat.py`<br>`tests/test_chat_api.py`<br>`tests/test_execution_api.py`<br>`tests/test_history_api.py`<br>`tests/test_memory_evidence_selection.py`<br>`tests/test_mind_gate.py` | — | — | 旧 Chat 召回门控；认知链不依赖 |
| `Mind/decision_log.py` | 删除 | `scripts/mind_promotion_controls.py`<br>`tests/test_memory_evidence_selection.py`<br>`tests/test_mind_gate.py`<br>`tests/test_mind_promotion_controls.py` | `tests/test_semantic_associative_v4_chat.py` | `README.md` | 旧 Chat 召回门控；认知链不依赖 |
| `Mind/docs/CHAT_RECALL_GATE.md` | 删除 | — | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`README.md`<br>`docs/CURRENT_STATUS.md`<br>`docs/EXPERIMENTS.md` | 旧 Chat 召回门控；认知链不依赖 |
| `Mind/docs/INTEGRATED_CHAIN.md` | 改写 | — | — | `AGENTS.md`<br>`README.md`<br>`docs/CURRENT_STATUS.md`<br>`docs/LUMINA_EXECUTION_FINAL_ARCHITECTURE.md`<br>`docs/MIND_DESIGN.md`<br>`docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md`<br>`docs/TASK_memory_v1.md`<br>`docs/plan/INTENTION_NERVOUS_STAGE1.md` | 去除旧门控说明 |
| `Mind/evidence_selector.py` | 删除 | `Conversation_Memory/tests/test_semantic_recall_v5.py`<br>`Conversation_Memory/tests/test_semantic_recall_v6.py`<br>`Conversation_Memory/tests/test_semantic_recall_v7.py`<br>`tests/test_memory_evidence_selection.py`<br>`tests/test_semantic_associative_chat.py`<br>`tests/test_semantic_associative_v2_chat.py`<br>`tests/test_semantic_associative_v3_chat.py`<br>`tests/test_semantic_associative_v4_chat.py`<br>`tests/test_semantic_associative_v5_chat.py`<br>`tests/test_semantic_associative_v6_chat.py`<br>`tests/test_semantic_associative_v7_chat.py` | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 Chat 召回门控；认知链不依赖 |
| `Mind/interfaces.py` | 删除 | `Mind/constant_gate.py`<br>`Mind/decision_log.py`<br>`Mind/llm_gate.py`<br>`tests/test_mind_gate.py`<br>`tests/test_mind_llm_gate.py`<br>`tests/test_mind_promotion_controls.py`<br>`tests/test_query_mind_gate.py` | — | — | 旧 Chat 召回门控；认知链不依赖 |
| `Mind/llm_gate.py` | 删除 | `scripts/mind_gate_shadow.py`<br>`tests/test_chat_api.py`<br>`tests/test_mind_llm_gate.py`<br>`tests/test_query_mind_chat.py`<br>`tests/test_query_mind_gate.py` | — | `README.md`<br>`docs/EXPERIMENTS.md` | 旧 Chat 召回门控；认知链不依赖 |
| `README.md` | 改写 | — | — | `AGENTS.md`<br>`Lumina_Canvas/欢迎.md`<br>`Memory_lab/docs/TASK_CARD.md`<br>`Memory_lab/docs/TASK_answer_v2.md`<br>`Memory_lab/docs/TASK_v3.md`<br>`docs/CURRENT_STATUS.md`<br>`docs/EXPERIMENTS.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`<br>`docs/TASK_memory_v1.md`<br>`docs/final_goal.md` | 更新正式入口和事实 |
| `core/cold_draft_store.py` | 改写 | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`Conversation_Memory/tests/test_body_memory_persistence.py`<br>`Dream/runner.py`<br>`Dream/tests/test_dream_cold_draft_digest.py`<br>`Dream/tests/test_dream_progress.py`<br>`core/hot_draft_compactor.py`<br>`core/main.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_cold_draft_progress.py`<br>`tests/test_cold_draft_store.py`<br>`tests/test_cold_source_window.py`<br>`tests/test_hot_draft_compactor.py`<br>`tests/test_memory_v1_chat.py`<br>`tests/test_message_runtime.py` | `Conversation_Memory/tests/test_entity_ingestion_v2.py`<br>`Conversation_Memory/tests/test_first_hit_ingestion.py`<br>`Conversation_Memory/tests/test_reliable_formation.py`<br>`Conversation_Memory/tests/test_reliable_recall.py`<br>`Conversation_Memory/tests/test_reliable_v5_ingestion.py` | `README.md` | 更新正式入口和事实 |
| `core/contracts.py` | 改写 | `Dream/tests/test_dream_cold_draft_digest.py`<br>`Memory_lab/analysis/memory_v1_chat_consistency.py`<br>`core/cold_draft_store.py`<br>`core/draft_context.py`<br>`core/draft_store.py`<br>`core/hot_draft_compactor.py`<br>`core/main.py`<br>`core/memory_adapter.py`<br>`core/message_runtime.py`<br>`core/model_client.py`<br>`core/turn_provenance.py`<br>`scripts/mind_promotion_controls.py`<br>`scripts/recall_e2e_test.py`<br>`tests/test_cold_draft_store.py`<br>`tests/test_draft_store.py`<br>`tests/test_grounded_historical_claim_guard.py`<br>`tests/test_history_api.py`<br>`tests/test_hot_draft_compactor.py`<br>`tests/test_memory_evidence_selection.py`<br>`tests/test_memory_v1_chat.py`<br>`tests/test_message_runtime.py`<br>`tests/test_mind_gate.py`<br>`tests/test_model_client.py` | `Conversation_Memory/tests/entity_memory_acceptance.py` | `README.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `core/evidence_acquisition.py` | 删除 | `tests/test_evidence_acquisition.py` | — | `Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md`<br>`docs/EXPERIMENTS.md` | 删除失效路径 |
| `core/main.py` | 改写 | `Dream/tests/test_dream_cold_draft_digest.py`<br>`tests/test_chat_api.py`<br>`tests/test_execution_api.py`<br>`tests/test_history_api.py`<br>`tests/test_memory_v1_chat.py`<br>`tests/test_recall_e2e_script.py`<br>`tests/test_semantic_associative_chat.py`<br>`tests/test_semantic_associative_v2_chat.py`<br>`tests/test_semantic_associative_v3_chat.py`<br>`tests/test_semantic_associative_v4_chat.py`<br>`tests/test_semantic_associative_v5_chat.py`<br>`tests/test_semantic_associative_v6_chat.py`<br>`tests/test_semantic_associative_v7_chat.py` | `Dream/tests/test_dream_progress.py`<br>`tests/test_memory_evidence_selection.py` | `AGENTS.md`<br>`Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`Memory_lab/docs/TASK_CARD.md`<br>`README.md`<br>`docs/EXPERIMENTS.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `core/message_runtime.py` | 改写 | `core/main.py`<br>`tests/test_grounded_historical_claim_guard.py`<br>`tests/test_memory_evidence_selection.py`<br>`tests/test_memory_v1_chat.py`<br>`tests/test_message_runtime.py`<br>`tests/test_mind_gate.py`<br>`tests/test_mind_promotion_controls.py`<br>`tests/test_semantic_associative_v4_chat.py`<br>`tests/test_semantic_associative_v5_chat.py`<br>`tests/test_semantic_associative_v6_chat.py`<br>`tests/test_semantic_associative_v7_chat.py` | — | `README.md`<br>`docs/EXPERIMENTS.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `core/model_client.py` | 改写 | `Dream/tests/test_dream_cold_draft_digest.py`<br>`Mind/evidence_selector.py`<br>`Mind/llm_gate.py`<br>`core/hot_draft_compactor.py`<br>`core/main.py`<br>`core/message_runtime.py`<br>`scripts/mind_gate_shadow.py`<br>`tests/test_chat_api.py`<br>`tests/test_evidence_acquisition.py`<br>`tests/test_execution_api.py`<br>`tests/test_memory_v1_chat.py`<br>`tests/test_message_runtime.py`<br>`tests/test_mind_gate.py`<br>`tests/test_mind_llm_gate.py`<br>`tests/test_model_client.py`<br>`tests/test_query_mind_chat.py`<br>`tests/test_recall_e2e_script.py`<br>`tests/test_semantic_associative_v6_chat.py`<br>`tests/test_semantic_associative_v7_chat.py` | `Conversation_Memory/tests/entity_memory_acceptance.py`<br>`Conversation_Memory/tests/test_grounded_formation.py`<br>`Dream/tests/test_dream_progress.py`<br>`tests/test_semantic_associative_v5_chat.py` | `Memory_lab/docs/DESIGN.md`<br>`Memory_lab/docs/DESIGN_v5.md`<br>`Memory_lab/docs/TASK_CARD.md`<br>`Memory_lab/docs/TASK_answer_v2.md`<br>`Memory_lab/docs/TASK_v3.md`<br>`Memory_lab/docs/TASK_v5.md`<br>`Memory_lab/prompts/answer_v1.md`<br>`README.md` | 更新正式入口和事实 |
| `docs/COLD_DRAFT.md` | 改写 | — | — | `AGENTS.md`<br>`Conversation_Memory/AGENTS.md`<br>`Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`Dream/AGENTS.md`<br>`README.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `docs/CURRENT_STATUS.md` | 改写 | — | — | `AGENTS.md`<br>`Conversation_Memory/AGENTS.md`<br>`Dream/AGENTS.md`<br>`Mind/AGENTS.md`<br>`Mind/docs/EXPERIMENT_HISTORY.md`<br>`Mind/docs/INTEGRATED_CHAIN.md`<br>`README.md`<br>`docs/RECOVERY_AND_WORKING_CONTEXT_DESIGN.md`<br>`docs/TASK_memory_v1.md`<br>`docs/plan/CODEX_INTENTION_NERVOUS_STAGE1.md`<br>`docs/plan/INTENTION_NERVOUS_STAGE1.md` | 更新正式入口和事实 |
| `docs/DRAFT_TURN_PROVENANCE_V2.md` | 改写 | — | — | `AGENTS.md`<br>`README.md` | 更新正式入口和事实 |
| `docs/EXPERIMENTS.md` | 改写 | — | — | `AGENTS.md`<br>`Lumina_Canvas/欢迎.md`<br>`README.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `docs/MAGMA_RECALL_ALGORITHM_AUDIT.md` | 删除 | — | — | `docs/TASK_memory_v1.md` | 删除失效路径 |
| `docs/MEMORY_EXPERIMENT_HISTORY.md` | 改写 | — | — | `AGENTS.md`<br>`docs/MAGMA_RECALL_ALGORITHM_AUDIT.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `docs/RECALL_E2E_ACCEPTANCE.md` | 删除 | — | — | `AGENTS.md`<br>`Conversation_Memory/AGENTS.md`<br>`Dream/AGENTS.md`<br>`docs/TASK_memory_v1.md` | 删除失效路径 |
| `docs/final_goal.md` | 改写 | — | — | `AGENTS.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 更新正式入口和事实 |
| `docs/plan/CODEX_EXECUTION_SELF_OBSERVATION_SLICE.md` | 改写 | — | — | — | 更新正式入口和事实 |
| `docs/plan/CODEX_INTENTION_NERVOUS_STAGE1.md` | 改写 | — | — | — | 更新正式入口和事实 |
| `docs/plan/INTENTION_NERVOUS_STAGE1.md` | 改写 | — | — | `Mind/docs/INTENTION_STAGE1.md`<br>`docs/plan/CODEX_INTENTION_NERVOUS_STAGE1.md` | 更新正式入口和事实 |
| `docs/plan/MIND_DEFINITION_V1.md` | 改写 | — | — | — | 更新正式入口和事实 |
| `edge/static/app.js` | 改写 | — | `tests/test_memory_v1_chat.py` | `docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `experiments/mind_relation.py` | 删除 | — | `tests/test_mind_relation_shadow.py` | `docs/EXPERIMENTS.md`<br>`docs/TASK_memory_v1.md` | 删除失效路径 |
| `pyproject.toml` | 改写 | — | — | `AGENTS.md`<br>`Memory_lab/docs/TASK_CARD.md`<br>`docs/TASK_memory_v1.md` | 更新正式入口和事实 |
| `scripts/first_hit_memory_check.py` | 删除 | — | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 删除失效路径 |
| `scripts/mind_gate_operational.py` | 删除 | `tests/test_mind_gate_operational.py` | — | — | 删除失效路径 |
| `scripts/mind_gate_shadow.py` | 删除 | `experiments/mind_relation.py`<br>`tests/test_mind_gate_shadow.py` | — | — | 删除失效路径 |
| `scripts/mind_promotion_controls.py` | 删除 | `tests/test_mind_promotion_controls.py` | — | — | 删除失效路径 |
| `scripts/recall_e2e_test.py` | 删除 | `tests/test_recall_e2e_script.py` | — | `Conversation_Memory/AGENTS.md`<br>`Conversation_Memory/docs/COLD_DRAFT_ADAPTER_DESIGN.md`<br>`Dream/docs/DREAM_COLD_DRAFT_DIGESTION.md`<br>`docs/RECALL_E2E_ACCEPTANCE.md` | 删除失效路径 |
| `tests/test_body_memory_chat.py` | 删除 | — | — | `Conversation_Memory/docs/BODY_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_calibrated_memory_chat.py` | 删除 | — | — | `README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_chat_api.py` | 改写 | — | `tests/test_history_api.py` | `README.md`<br>`docs/EXPERIMENTS.md` | 保留行为并改测新路径 |
| `tests/test_cold_draft_progress.py` | 改写 | — | — | — | 保留行为并改测新路径 |
| `tests/test_cold_source_window.py` | 改写 | — | — | — | 保留行为并改测新路径 |
| `tests/test_draft_store.py` | 改写 | — | — | — | 保留行为并改测新路径 |
| `tests/test_evidence_acquisition.py` | 改写 | — | — | `Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md`<br>`docs/EXPERIMENTS.md` | 保留行为并改测新路径 |
| `tests/test_execution_api.py` | 改写 | — | — | `AGENTS.md`<br>`README.md` | 保留行为并改测新路径 |
| `tests/test_first_hit_memory_check.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_grounded_historical_claim_guard.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_history_api.py` | 改写 | — | — | — | 保留行为并改测新路径 |
| `tests/test_hot_draft_compactor.py` | 改写 | — | — | — | 保留行为并改测新路径 |
| `tests/test_memory_evidence_selection.py` | 删除 | — | — | `README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_message_runtime.py` | 改写 | — | — | `README.md` | 保留行为并改测新路径 |
| `tests/test_mind_gate.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_mind_gate_operational.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_mind_gate_shadow.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_mind_llm_gate.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_mind_promotion_controls.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_mind_relation_shadow.py` | 删除 | — | — | `docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_model_client.py` | 改写 | — | — | `Conversation_Memory/docs/SOURCE_REPRESENTATION_PROTOTYPE.md`<br>`README.md` | 保留行为并改测新路径 |
| `tests/test_query_mind_chat.py` | 删除 | `tests/test_body_memory_chat.py`<br>`tests/test_calibrated_memory_chat.py` | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_query_mind_gate.py` | 删除 | `tests/test_query_mind_chat.py` | — | `Conversation_Memory/docs/FIRST_HIT_MEMORY.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_recall_e2e_script.py` | 删除 | — | — | — | 只测已删路径 |
| `tests/test_semantic_associative_chat.py` | 删除 | — | — | `README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_semantic_associative_v2_chat.py` | 删除 | — | — | `README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_semantic_associative_v3_chat.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_semantic_associative_v4_chat.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_semantic_associative_v5_chat.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_semantic_associative_v6_chat.py` | 删除 | `tests/test_semantic_associative_v7_chat.py` | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |
| `tests/test_semantic_associative_v7_chat.py` | 删除 | — | — | `Conversation_Memory/docs/RELIABLE_MEMORY.md`<br>`README.md`<br>`docs/EXPERIMENTS.md` | 只测已删路径 |

| `Conversation_Memory/engine/config.py` | 改写 | `Memory_lab/analysis/check_cache_v51.py`<br>`Memory_lab/analysis/check_cache_v52.py`<br>`Memory_lab/analysis/counterfactual_v5.py`<br>`Memory_lab/analysis/stage1_v51.py`<br>`Memory_lab/analysis/stage1_v52.py`<br>`Memory_lab/lab/replay.py` | `Memory_lab/tests/test_answer_v2.py`<br>`Memory_lab/tests/test_assoc_v5.py`<br>`Memory_lab/tests/test_integrate_prompt_v3.py`<br>`Memory_lab/tests/test_integration_cache.py`<br>`Memory_lab/tests/test_measure.py`<br>`Memory_lab/tests/test_more_contracts.py`<br>`Memory_lab/tests/test_pattern_v5.py`<br>`Memory_lab/tests/test_recall_scoring.py` | — | 随正式记忆 v1 改写 |
| `Conversation_Memory/engine/integrate.py` | 改写 | `Memory_lab/analysis/check_cache_v51.py`<br>`Memory_lab/analysis/check_cache_v52.py`<br>`Memory_lab/lab/replay.py`<br>`Memory_lab/lab/usage_judge.py` | `Memory_lab/tests/test_integrate_prompt_v3.py`<br>`Memory_lab/tests/test_integration_cache.py`<br>`Memory_lab/tests/test_more_contracts.py`<br>`Memory_lab/tests/test_recall_scoring.py`<br>`Memory_lab/tests/test_usage_integrated_v5.py`<br>`Memory_lab/tests/test_usage_v3.py`<br>`Memory_lab/tests/test_v51.py`<br>`Memory_lab/tests/test_v52.py` | — | 随正式记忆 v1 改写 |
| `Conversation_Memory/engine/pattern.py` | 删除 | `Memory_lab/analysis/metrics_v51.py`<br>`Memory_lab/analysis/stage1_v51.py`<br>`Memory_lab/lab/replay.py` | `Memory_lab/tests/test_pattern_v5.py`<br>`Memory_lab/tests/test_v51.py`<br>`Memory_lab/tests/test_v52.py` | — | 旧预设专用提示词或算法 |
| `Conversation_Memory/engine/pattern_v2.py` | 改写 | `Memory_lab/analysis/stage1_v51.py`<br>`Memory_lab/lab/replay.py` | `Memory_lab/tests/test_v51.py` | — | 随正式记忆 v1 改写 |
| `Conversation_Memory/engine/pattern_v3.py` | 改写 | `Memory_lab/lab/replay.py` | `Memory_lab/tests/test_v52.py` | — | 随正式记忆 v1 改写 |
| `Conversation_Memory/facade.py` | 改写 | `Dream/runner.py`<br>`Memory_lab/analysis/memory_v1_chat_consistency.py`<br>`core/main.py`<br>`core/message_runtime.py` | `tests/test_memory_v1_chat.py`<br>`tests/test_memory_v1_facade.py` | `Memory_lab/AGENTS.md` | 随正式记忆 v1 改写 |
| `Conversation_Memory/prompts/integrate_v1.md` | 删除 | — | — | — | 旧预设专用提示词或算法 |
| `Conversation_Memory/prompts/integrate_v2.md` | 删除 | — | — | — | 旧预设专用提示词或算法 |
| `Conversation_Memory/prompts/integrate_v3.md` | 删除 | — | — | — | 旧预设专用提示词或算法 |
| `Conversation_Memory/prompts/pattern_v1.md` | 删除 | — | — | — | 旧预设专用提示词或算法 |
| `Lumina_Canvas/.obsidian/workspace.json` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/analysis/account_v51.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/account_v52.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/analyze_v51.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/analyze_v52.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/build_report_v51.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/build_report_v52.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/build_results_v5.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/check_cache_v51.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/check_cache_v52.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/counterfactual_v5.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/metrics_v51.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/analysis/prepare_j1_v52.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/stage1_v51.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/stage1_v52.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/targeted_answer_v5.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/analysis/targeted_v51.py` | 删除 | — | — | — | 旧实验阶段专用脚本 |
| `Memory_lab/lab/cli.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/lab/repair.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_answer_noise_v4.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_answer_v2.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_answer_v4.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_assoc_v5.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_dream_usage_replay_v4.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_integrate_prompt_v3.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_integration_cache.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_judge_v3.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/tests/test_measure.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/tests/test_more_contracts.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/tests/test_pattern_v5.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_recall_scoring.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_render_reason_v3.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_repair_v4.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_usage_integrated_v5.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_usage_judge_v4.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_usage_v3.py` | 删除 | — | — | — | 旧预设专用路径或测试 |
| `Memory_lab/tests/test_v51.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `Memory_lab/tests/test_v52.py` | 改写 | — | — | — | 随正式记忆 v1 改写 |
| `requirements.txt` | 改写 | — | — | `Memory_lab/docs/TASK_CARD.md`<br>`Mind/docs/REPETITION_REASSESSMENT_RESULT.md`<br>`README.md` | 随正式记忆 v1 改写 |

## 基线与阶段 1

原始 HEAD：`c71317712f07851abe311a07140679cb1d25b8fa`；本地标签：`archive/pre-memory-v1`；本地分支：`memory-v1`。

- 全树基线：79 failed、2352 passed、10 skipped；失败集中在未初始化的旧 MAGMA 依赖。
- 认知链基线：690 passed、6 skipped（外层权限，沙箱 socket 限制会产生 31 个假失败）。
- 实验室基线：128 passed。
- R0：`Memory_lab/runs/memory_v1/r0_dev_a_P8`、`r0_dev_b_P8`；与 `Memory_lab/runs/v51/dev_a_P8`、`dev_b_P8` 的指定记忆侧字段均为 0 差异。
- 搬迁后：`Memory_lab/runs/memory_v1/moved_dev_a_P8`、`moved_dev_b_P8` 对 R0 为 0 差异；实验室 128 passed。

## 阶段 2

- Chat 转换路径对 dev_a、dev_b 各 60 个探针的记忆块逐字一致；cache-only，0 新模型调用。目录：`Memory_lab/runs/memory_v1/consistency_dev_a_P8`、`consistency_dev_b_P8`。
- 阶段 2 定向离线测试：12 passed。

## 阶段 3

旧 `adapter/`、`ingestion/`、`recall/`、MAGMA 子模块、同步 Dream、Chat 门控及其专属测试已按盘点表删除。删除子模块前确认检出目录无未跟踪文件，且检出的 HEAD 与 gitlink 一致；`.venv`、BGE 权重缓存、`data/` 和 `.env.local` 均未移动或暂存。旧实验室文档 19 份以 `git mv` 移至 `Memory_lab/docs/history/`；B1/P8/P9 与评测集、盲评工具继续保留。旧预设专用分析脚本、提示词和测试也已清理；B1 仍用的 `answer_v2.md` 从归档标签按原字节恢复，SHA-256 为 `7f51cdf24293a706318db0ef23fb0b1e17bc35be95cdacbaed1d74792002cf3b`。`pattern.py` 中 P8/P9 共用的三个小型持久化函数移至 `pattern_common.py`。

清理后 cache-only 回放为 `Memory_lab/runs/memory_v1/final2_dev_a_P8` 与 `final2_dev_b_P8`，对各自 R0 的指定记忆侧字段均为 **0 差异**，新增模型调用 0。`git grep` 对代码和配置文件检查 `MAGMA`、`FirstHit`、`semantic-associative`、`LUMINA_MIND_GATE_MODE`、`Conversation_Memory.adapter`，无运行时引用；`git diff --check` 与 `git diff --cached --check` 均通过。历史文档保留旧词作为归档事实。

| 离线命令 | 修改前基线 | 清理后 | 结论 |
| --- | --- | --- | --- |
| `Conversation_Memory/.venv/bin/python -m pytest -q` | 79 failed、2352 passed、10 skipped（旧 MAGMA 未初始化） | 813 passed、6 skipped、1 Starlette warning | 完整复跑无新增失败。首跑曾因未改动的 Execution 测试 3 秒并发等待超时出现 1 失败；该测试单跑通过，随后完整复跑通过。 |
| `Conversation_Memory/.venv/bin/python -m pytest Mind Nervous Execution -q` | 690 passed、6 skipped | 690 passed、6 skipped | 认知链通过，行为代码未修改。 |
| 在 `Memory_lab/` 运行 `../Conversation_Memory/.venv/bin/python -m pytest tests -q` | 128 passed | 80 passed | 48 个旧预设/旧路径测试随旧实现删除；保留测试无失败。 |
| `Conversation_Memory/.venv/bin/python -m pytest tests -q` | 基线包含于全树 | 123 passed、1 Starlette warning | Chat、Cold、Execution API 和记忆 v1 接口通过。 |

### 正式结构与入口

| 旧入口 | 当前入口 | 说明 |
| --- | --- | --- |
| `Memory_lab/memlab/` | `Conversation_Memory/engine/` | P8 图、召回、整合、规律与存储的唯一实现；实验室从此导入。 |
| 旧 Chat adapter / 多分支召回门控 | `Conversation_Memory/facade.py`、`core/message_runtime.py` | 每轮本地召回、answer_v5 一次回答、Hot 与痕迹写入、压缩、自动 Dream 检查。 |
| 旧同步 Cold 消化 | `Dream/runner.py`、`MemoryV1.dream_once` | 独立 Cold 游标；每次一个窗口；支持显式 `run`、`rebuild`、`cursor-end`。 |
| 实验室专用回答拼法 | `Conversation_Memory/answer.py` | Chat 与实验室共用，实验室摘要提示词冻结在 `Memory_lab/lab/summary_prompt.py`。 |

### 接入与配置

Chat 读取完整原始 Hot 与滚动摘要，借 `core/memory_adapter.py` 按 `LUMINA_DEFAULT_TIMEZONE` 将 DraftTurn 转为记忆输入；调用纯读召回；共用 answer_v5 的带时间 Hot、时间间隔和有截止日期的摘要拼法；回答后写 Hot 与逐轮来源，再以 assistant 轮次 ID 追加含三栏内容的痕迹。Cold 压缩和摘要随后执行。召回失败时走空记忆；回答的私有三栏不会出现在对用户的回复。实验室两集各 60 个探针，经 Chat 转换路径生成的记忆块与 `rendered` **逐字节相同**，0 模型调用。答复真实预填验证成功：最后一条 assistant 为 `{"理解": "`，1 次 DeepSeek-V4-Pro 调用；Chat 的 Pro 客户端已启用预填。

| 配置 | 作用与默认值 |
| --- | --- |
| `LUMINA_MODEL_MODE` / `DEEPSEEK_API_KEY` | 原有真实/Mock 开关及凭据；回答和滚动摘要仍使用 `deepseek-v4-pro`。 |
| `LUMINA_CONVERSATION_MEMORY_RECALL_ENABLED` | 默认启用每轮本地召回；关闭时不读记忆。 |
| `LUMINA_MEMORY_MODEL` | 记忆整合和归纳的独立客户端，默认精确模型 ID `deepseek-flash`。 |
| `LUMINA_MEMORY_DIR` | 默认 `data/memory_v1/`，与旧 MAGMA 目录隔离。 |
| `LUMINA_DREAM_TRIGGER_TURNS` | 默认 40；按未整合 Cold 轮次计数。 |
| `LUMINA_EMBED_MODEL_PATH` | 可选本地 BGE-M3 快照路径；生产固定 revision `5617a9f61b028005a4858fdac845db406aefb181` 和权重 SHA-256 `b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`。 |
| `LUMINA_DRAFT_STORE_PATH` / `LUMINA_DEFAULT_TIMEZONE` | 保留原有 Hot/Cold 路径与时区规则，默认 `Asia/Shanghai`。 |

自动 Dream 只在真实模型、嵌入可用、已设置 Cold 游标、未暂停且达到阈值时，于回复送出后启动后台线程。独立 Dream 锁使忙碌调用跳过；模型调用时不持有 Chat 写锁，只有 Cold 快照和 SQLite 提交短暂协调。同窗失败连续 3 次暂停自动触发，人工运行成功后清除暂停。`/api/status` 报记忆/规律数、Cold 未整合轮次、最近 Dream、暂停与嵌入状态；`/api/memory` 只读返回正文、时间标签、π、规律、来源数与日志；`/api/dream/run` 返回新 Dream 结果，前端与 CLI 同步更新。滚动摘要独立使用 2000 token，上限停止原因触发 3000 token 一次重试；再次截断时保留旧摘要，Cold 原文仍保存。旧摘要无截止时间时省略日期行。

## 阶段 4

### 隔离真实调用

开跑前预算为预填 1、回答 4、摘要约 2、整合约 2、归纳至多 2。所有 Hot、Cold、记忆 SQLite 与模型响应缓存均在自动删除的临时目录；合成 Cold 预置 3 个逻辑日 6 轮，阈值为 2，Hot 保留 2 轮且超过 4 轮压缩。两轮独立烟测用于保留失败证据：第一轮 Dream `d1` 有 1 条被拒写入、`d2` 无操作，记忆数 0，第三条回答出现回退；第二轮改用明确稳定事实，4 个 `/api/chat` 均 HTTP 200 且为真实模型回答，自动 Dream 两次均 applied，0 拒写，`/api/memory` 可见 2 条记忆，4 条带三栏的痕迹，压缩已完成，Dream 期间聊天未出现 409。第二轮的规律调用产生候选但模型没有写入规律。随后在独立临时 P8 状态中，用跨 3 日的焦虑—散步—平静材料直接验证真实规律路径：4 组候选、1 次 flash 调用，写出 3 条规律。这个补充探针验证规律写入机制，但不等同于 Chat 自动 Dream 的端到端规律结果。

| 调用阶段 | 成功模型调用 | 输入 token | 输出 token | 可见结果 |
| --- | ---: | ---: | ---: | --- |
| answer_v5 预填验证 | 1 | 357 | 61 | 接口接受预填且可解析回复 |
| 第一轮合成烟测（Pro 5、flash 2） | 7 | 3015 | 428 | 记忆拒写、1 次回答回退 |
| 第二轮合成烟测（Pro 5、flash 3） | 8 | 3243 | 655 | 4 次回答、2 次 Dream、2 条记忆；规律未写 |
| 补充规律探针（flash 1） | 1 | 449 | 214 | 4 组候选写出 3 条规律 |
| **合计** | **17** | **7064** | **1358** | **8422 token** |

表中是客户端确认收到的模型响应和其 usage；第一轮 1 次 fallback 仍计入真实响应。客户端未记录 HTTP 层 400 兼容重发等失败请求，因此原始网络请求数不可从运行收据精确复原。

### 真实 Cold 与预算闸门

服务当时未运行。按服务环境解析 Hot、Cold、压缩游标、旧消化游标和 MAGMA 目录后，在仓库外建立 `../Lumina-memory-v1-backup-20260927/MANIFEST.json`，清单记录这些路径均不存在：真实 Cold **0 轮、0 字、0 个可重建窗口、估算 0 输入 token**；无旧片段可跳过，也无真实数据可复制或重建。旧 MAGMA 数据不存在且未改动。故按任务卡“不存在真实 Cold”闸门未执行重建，也无真实记忆 10 条样本或规律原文可报告。服务停止后如有实际 Cold，命令为 `Conversation_Memory/.venv/bin/python -m Dream.runner rebuild --memory-dir data/memory_v1 --cold-path data/draft/cold_drafts.jsonl`；它按真实文字估算并在累计输入超过 50 万 token 时停在已完成游标。

## 偏差与未解决问题

1. 第一轮真实烟测的合成材料没有成功写入记忆，且一次回答回退；第二轮更明确的稳定事实产生 2 条记忆，说明写入成败受整合响应质量影响。证据是两轮 Dream 收据（首轮 `d1` 1 拒写，次轮 `d1`/`d2` 均 0 拒写）与 API 结果。
2. 第二轮端到端烟测有规律候选但模型选择不写；直接 P8 规律探针 4 组候选写出 3 条。这满足真实规律写入机制的验证，但**没有满足任务卡对同一次自动 Dream 烟测“有候选组时写出规律”的强断言**。保留这个负结果，没有为了通过验收强制写入模型判为 `none` 的规律。
3. 没有真实 Cold，故阶段 4 的生产历史重建、10 条记忆抽样和全部规律正文均不可执行；0 字估算和备份清单如上。
4. Windows 目标平台本次未验证。Linux 测试和真实调用不能替代 Windows 安装与运行验收。
5. 模型调用数和 token 以成功响应的 usage 为准，原始 HTTP 重发数不可恢复；这是 20 次预算核算的可观测性限制。
6. 最初的删除前盘点覆盖 652 个文件；清理过程中再发现 49 个相关改动路径后，从未改动的归档 HEAD 回查引用并补成 701 行。因此这 49 行的记录时间晚于清理开始，但依据仍是删除前内容。

普通实现选择：为清理旧 pattern_v1 而保留 P8/P9 共用的持久化函数于 `pattern_common.py`；`MemoryV1.inspect` 列出全部规律，即便一般记忆列表仍限 100 条；缺失真实 Cold 时保持新库未初始化游标，防止自动消化未知历史。

## 下一步建议

先在实际部署机器核对 Cold 路径与 Windows 依赖；若有真实 Cold，再在停服和 50 万输入 token 闸门下重建。若要宣称自动 Dream 能稳定归纳规律，需要设计更多有明确跨日关系的合成或授权真实案例，记录模型选择 `none` 的比例并复验，不应以本次单一补充探针代替。
