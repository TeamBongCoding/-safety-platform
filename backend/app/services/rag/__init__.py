"""RAG (Retrieval-Augmented Generation) 서비스 패키지.

구조
----
- embedding.py   : EmbeddingProvider 인터페이스 + BAAI/bge-m3 구현 + Mock
- indexer.py     : DocumentIndexer — 문서 chunking, 임베딩, DB 저장
- retriever.py   : KnowledgeRetriever — pgvector cosine 검색 / SQLite fallback
"""
