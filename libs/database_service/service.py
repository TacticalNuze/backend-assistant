#!/usr/bin/env python3
"""
Database Service for VectorRAG Pipeline
"""
import logging
import os
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class DatabaseService:
    """Database service with real storage manager and vector database provider"""
    
    def __init__(self):
        self.connected = False
        self.initialized = False
        # Initialize vector database service
        from .vector_db import VectorDatabaseService
        self.vector_manager = VectorDatabaseService()
        
        # Initialize document database service
        from .doc_db import DocumentDatabaseService
        self.document_manager = DocumentDatabaseService()
        
        # Initialize storage manager (used by other steps; optional for vector search)
        from .storage import MinIOStorageManager
        self.storage_manager = MinIOStorageManager(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            secure=str(os.getenv("MINIO_SECURE", "false")).lower() == "true",
        )
    
    async def initialize(self):
        """Initialize the database service"""
        try:
            # Initialize storage if available, but don't fail the whole service on storage errors
            if getattr(self, "storage_manager", None):
                try:
                    await self.storage_manager.initialize()
                except Exception as e:
                    logger.warning(f"Storage manager init failed (continuing): {e}")

            # Always initialize the vector manager (required for search)
            if getattr(self, "vector_manager", None):
                await self.vector_manager.initialize()

            # Initialize document database manager (optional, but recommended)
            if getattr(self, "document_manager", None):
                try:
                    await self.document_manager.initialize()
                except Exception as e:
                    logger.warning(f"Document manager init failed (continuing): {e}")

            self.initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize database service: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            self.initialized = False
            return False
    
    async def store_preprocessing_output(self, job_id: str, output_type: str, data: dict, metadata: dict):
        """Store preprocessing output using storage manager"""
        if not self.storage_manager:
            raise RuntimeError("Storage manager not initialized")
        return await self.storage_manager.store_preprocessing_output(job_id, output_type, data, metadata)
    
    #########################################################
    # Upload function Added
    #########################################################
    async def upload_file(
        self,
        file_data: bytes,
        filename: str,
        client_id: str,
        project_id: str,
        content_type: str = None
    ) -> str:
        """Upload file to object storage using storage manager"""
        if not self.storage_manager:
            raise RuntimeError("Storage manager not initialized")
        
        return await self.storage_manager.upload_file_with_structure(
            file_data=file_data,
            filename=filename,
            client_id=client_id,
            project_id=project_id,
            content_type=content_type
        )
    
    async def store_embedding(self, chunks_with_embeddings: List[Dict[str, Any]], client_id: str, project_id: str):
        """Store a single embedding (set of chunks with embeddings) in vector database using vector manager"""
        if not self.vector_manager:
            raise RuntimeError("Vector manager not initialized")
        return await self.vector_manager.store_embedding(chunks_with_embeddings, client_id, project_id)
    
    async def store_chunks(self, raw_chunk: Dict[str, Any], embedding: List[Dict[str, Any]], client_id: str, project_id: str):
        """Store chunks in vector database using vector manager
        
        Deprecated: Use store_embedding instead. This method is kept for backward compatibility.
        """
        logger.warning("store_chunks is deprecated, use store_embedding instead")
        return await self.vector_manager.store_embedding(embedding, client_id, project_id)
    
    async def search_chunks(self, query: str, client_id: str, project_id: str, top_k: int = 5):
        """Search chunks in vector database using vector manager"""
        if not self.vector_manager:
            raise RuntimeError("Vector manager not initialized")
        return await self.vector_manager.similarity_search(query, client_id, project_id, top_k)
    
    async def close(self):
        """Close database connections"""
        if hasattr(self.storage_manager, 'close'):
            await self.storage_manager.close()
        if hasattr(self.vector_manager, 'close'):
            await self.vector_manager.close()
        if hasattr(self, 'document_manager') and hasattr(self.document_manager, 'close'):
            await self.document_manager.close()
        self.connected = False