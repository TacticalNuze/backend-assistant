"""
Database Service for Kotaemon

This service handles database operations including vector databases (ChromaDB, Weaviate,
etc.), document databases (Elasticsearch),
and object storage (MinIO).
"""

from .storage import MinIOStorageManager
from .store_results import StoreResults, get_store_results
from .vector_db import  ChromaVectorProvider
from .doc_db import ElasticsearchDocProvider, DocumentDatabaseService
from .models import VectorIndexConfig
from .service import DatabaseService

__all__ = [
    # Storage management
    "MinIOStorageManager",
    "StoreResults",
    "get_store_results",
    
    # Vector database
    "WeaviateVectorProvider",
    "ChromaVectorProvider",
    
    # Document database
    "ElasticsearchDocProvider",
    "DocumentDatabaseService",
    
    # Data models
    "VectorIndexConfig",
    
    # Service classes
    "DatabaseService",
]