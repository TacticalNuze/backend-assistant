"""
Document Database Service

This module provides an abstraction layer for document database operations,
allowing the system to work with different document database providers
without being tightly coupled to any specific implementation.

To add a new document database provider:
1. Create a new provider class in this directory that inherits from BaseDocProvider
2. Implement all required abstract methods from BaseDocProvider
3. Add a _create_{provider_name}_provider method to this service class
4. Add the provider to the provider_factory dictionary in _create_provider method
5. Update the __init__.py file to export the new provider
"""

import os
import logging
from typing import Dict, List, Any, Optional
from .base import BaseDocProvider
from .elasticsearch_provider import ElasticsearchDocProvider

logger = logging.getLogger(__name__)


class DocumentDatabaseService:
    """Service for managing document database operations across different providers"""
    
    def __init__(self, doc_db_type: Optional[str] = None):
        """
        Initialize the document database service
        
        Args:
            doc_db_type: Type of document database to use. If None, will use DOC_DB_TYPE env var
        """
        self.doc_db_type = doc_db_type or os.getenv("DOC_DB_TYPE", "elasticsearch").lower()
        self.provider: Optional[BaseDocProvider] = None
        self._initialized = False
    
    async def initialize(self) -> bool:
        """Initialize the document database provider"""
        
        try:
            logger.info(f"Initializing document database service with type: {self.doc_db_type}")
            self.provider = self._create_provider()
            
            if self.provider:
                logger.info(f"Created {self.doc_db_type} provider, initializing...")
                success = await self.provider.initialize()
                
                if success:
                    self._initialized = True
                    logger.info(f"Document database service initialized with {self.doc_db_type} provider")
                else:
                    logger.error(f"Failed to initialize {self.doc_db_type} provider")
                return success
            else:
                logger.error(f"Failed to create {self.doc_db_type} provider")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize document database service: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
    
    def _create_provider(self) -> Optional[BaseDocProvider]:
        """Create the appropriate document database provider based on configuration"""
        
        try:
            # Factory pattern for creating document database providers
            provider_factory = {
                "elasticsearch": self._create_elasticsearch_provider,
            }
            
            if self.doc_db_type in provider_factory:
                provider = provider_factory[self.doc_db_type]()
                return provider
            else:
                logger.warning(f"Unsupported document database type '{self.doc_db_type}', defaulting to Elasticsearch")
                return self._create_elasticsearch_provider()
        except Exception as e:
            logger.error(f"Failed to create document database provider: {e}")
            return None
    
    def _create_elasticsearch_provider(self) -> ElasticsearchDocProvider:
        """Create an Elasticsearch provider instance"""
        return ElasticsearchDocProvider()
    
    # ==================== Basic CRUD Operations ====================
    
    async def save_document(
        self, 
        index: str, 
        doc_id: str, 
        data: Dict[str, Any], 
        client_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> bool:
        """
        Save or update a document in the document database
        
        Args:
            index: Name of the index to store the document
            doc_id: Unique identifier for the document
            data: Document data to save
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            True if successful, False otherwise
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.save(index, doc_id, data, client_id, project_id)
        except Exception as e:
            logger.error(f"Failed to save document: {e}")
            raise
    
    async def get_document(
        self, 
        index: str, 
        doc_id: str, 
        client_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a document by ID
        
        Args:
            index: Name of the index
            doc_id: Document ID to retrieve
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            Document data if found, None otherwise
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.load(index, doc_id, client_id, project_id)
        except Exception as e:
            logger.error(f"Failed to get document: {e}")
            return None
    
    async def update_document(
        self, 
        index: str, 
        doc_id: str, 
        data: Dict[str, Any], 
        client_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> bool:
        """
        Update an existing document (same as save, but semantically clearer)
        
        Args:
            index: Name of the index
            doc_id: Document ID to update
            data: Updated document data
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            True if successful, False otherwise
        """
        return await self.save_document(index, doc_id, data, client_id, project_id)
    
    async def delete_document(
        self, 
        index: str, 
        doc_id: str, 
        client_id: Optional[str] = None
    ) -> bool:
        """
        Delete a document from the document database
        
        Args:
            index: Name of the index
            doc_id: Document ID to delete
            client_id: Client identifier for data isolation
            
        Returns:
            True if successful, False otherwise
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.delete(index, doc_id, client_id)
        except Exception as e:
            logger.error(f"Failed to delete document: {e}")
            return False
    
    async def search_documents(
        self, 
        index: str, 
        query: Dict[str, Any], 
        size: int = 10,
        client_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Perform a search query in the document database
        
        Args:
            index: Name of the index to search
            query: Elasticsearch query DSL
            size: Maximum number of results to return
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            List of matching documents
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.search(index, query, size, client_id, project_id)
        except Exception as e:
            logger.error(f"Failed to search documents: {e}")
            return []
    
    # ==================== Bulk Operations ====================
    
    async def bulk_save_documents(
        self,
        index: str,
        documents: List[Dict[str, Any]],
        client_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Save multiple documents in bulk
        
        Args:
            index: Name of the index
            documents: List of documents, each with 'doc_id' and 'data' keys
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            Dictionary with 'saved' count and 'errors' list
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        saved = 0
        errors = []
        
        for doc in documents:
            try:
                doc_id = doc.get("doc_id")
                data = doc.get("data", {})
                
                if not doc_id:
                    errors.append({"document": doc, "error": "Missing doc_id"})
                    continue
                
                success = await self.save_document(index, doc_id, data, client_id, project_id)
                if success:
                    saved += 1
                else:
                    errors.append({"doc_id": doc_id, "error": "Save returned False"})
            except Exception as e:
                errors.append({"doc_id": doc.get("doc_id"), "error": str(e)})
        
        return {"saved": saved, "errors": errors, "total": len(documents)}
    
    async def bulk_delete_documents(
        self,
        index: str,
        doc_ids: List[str],
        client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Delete multiple documents in bulk
        
        Args:
            index: Name of the index
            doc_ids: List of document IDs to delete
            client_id: Client identifier for data isolation
            
        Returns:
            Dictionary with 'deleted' count and 'errors' list
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        deleted = 0
        errors = []
        
        for doc_id in doc_ids:
            try:
                success = await self.delete_document(index, doc_id, client_id)
                if success:
                    deleted += 1
                else:
                    errors.append({"doc_id": doc_id, "error": "Delete returned False"})
            except Exception as e:
                errors.append({"doc_id": doc_id, "error": str(e)})
        
        return {"deleted": deleted, "errors": errors, "total": len(doc_ids)}
    
    # ==================== Specialized Operations ====================
    
    async def create_document_mapping(
        self,
        index_name: str,
        document_id: str,
        storage_object_name: str,
        vector_chunk_ids: List[str],
        metadata: Dict[str, Any],
        client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a mapping document that links a storage object to its vector chunks
        
        Args:
            index_name: Name of the index
            document_id: Unique identifier for the mapping document
            storage_object_name: Name/key of the object in storage
            vector_chunk_ids: List of vector chunk UUIDs
            metadata: Additional metadata about the document
            client_id: Client identifier for data isolation
            
        Returns:
            Response from Elasticsearch with document ID
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.create_document_to_chunks_mapping(
                index_name, document_id, storage_object_name, vector_chunk_ids, metadata, client_id
            )
        except Exception as e:
            logger.error(f"Failed to create document mapping: {e}")
            raise
    
    async def delete_document_mapping(
        self,
        index_name: str,
        document_id: str,
        client_id: Optional[str] = None
    ) -> bool:
        """
        Delete a document mapping
        
        Args:
            index_name: Name of the index
            document_id: Document ID to delete
            client_id: Client identifier for data isolation
            
        Returns:
            True if successful, False otherwise
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.delete_document_mapping(index_name, document_id, client_id)
        except Exception as e:
            logger.error(f"Failed to delete document mapping: {e}")
            return False
    
    async def save_chunk_embeddings(
        self,
        index_name: str,
        file_name: str,
        chunks: List[Dict[str, Any]],
        client_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Save chunk embeddings for a single file to Elasticsearch in bulk
        
        Args:
            index_name: Name of the index
            file_name: Name of the file
            chunks: List of chunks with 'chunk_id' and 'embedding' keys
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            Dictionary with 'indexed' count and 'errors' list
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Document database service not initialized")
        
        try:
            return await self.provider.save_chunk_embedding_mapping_to_document_db(
                index_name, file_name, chunks, client_id, project_id
            )
        except Exception as e:
            logger.error(f"Failed to save chunk embeddings: {e}")
            raise
    
    # ==================== Health & Status ====================
    
    async def health_check(self) -> Dict[str, Any]:
        """Check the health of the document database service"""
        if not self._initialized or not self.provider:
            return {
                "status": "unhealthy",
                "error": "Service not initialized"
            }
        
        try:
            # Try to ping the provider's client
            if hasattr(self.provider, 'client') and self.provider.client:
                if hasattr(self.provider.client, 'ping'):
                    is_alive = self.provider.client.ping()
                    return {
                        "status": "healthy" if is_alive else "unhealthy",
                        "provider": self.doc_db_type,
                        "initialized": self._initialized
                    }
            
            return {
                "status": "healthy",
                "provider": self.doc_db_type,
                "initialized": self._initialized
            }
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "provider": self.doc_db_type
            }
    
    async def close(self):
        """Close the document database connection"""
        if self.provider and hasattr(self.provider, 'close'):
            try:
                await self.provider.close()
                logger.info("Document database connection closed")
            except Exception as e:
                logger.error(f"Error closing document database connection: {e}")
        
        self._initialized = False
        self.provider = None
    
    def is_initialized(self) -> bool:
        """Check if the service is initialized"""
        return self._initialized and self.provider is not None
    
    def get_provider_type(self) -> str:
        """Get the type of document database provider being used"""
        return self.doc_db_type

