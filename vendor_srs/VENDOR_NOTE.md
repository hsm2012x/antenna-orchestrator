# vendor_srs — 계승 코드 (수정 금지)

원 저장소 5_SRS_AI_Studio의 HANDOFF 스냅샷(2026-07-29)에서 복사. 정본은 원 저장소.
출처: handoff/02_tools/_antenna/ · handoff/03_interfaces/{retrieval_graphrag,pipeline_langgraph}/

- cad_render.py        식별·추출·렌더 (stdlib+Pillow)
- cad_viewer_server.py 뷰어 원형 (:8094로 확장 예정)
- chunker.py           청킹 규약
- llm_pool.py          vLLM OpenAI 호환 풀
- graph_rag/           그래프 계층 (주입식·LLM 0콜)

규칙: 이 폴더의 파일은 수정하지 않는다(AGENTS.md T-5). 수정이 필요하면 tools/에서 감싼다.
