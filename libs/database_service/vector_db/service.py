"""
Vector Database Service

This module provides an abstraction layer for vector database operations,
allowing the system to work with different vector database providers
without being tightly coupled to any specific implementation.

To add a new vector database provider:
1. Create a new provider class in this directory that inherits from BaseVectorProvider
2. Implement all required abstract methods from BaseVectorProvider
3. Add a _create_{provider_name}_provider method to this service class
4. Add the provider to the provider_factory dictionary in _create_provider method
5. Update the __init__.py file to export the new provider

Example:
    def _create_chroma_provider(self) -> ChromaVectorProvider:
        return ChromaVectorProvider(
            host=os.getenv("CHROMA_HOST", "localhost"),
            port=int(os.getenv("CHROMA_PORT", "8000"))
        )
"""

import os
import logging
from typing import Dict, List, Any, Optional
from .base import BaseVectorProvider
from .chroma_provider import ChromaVectorProvider
from ..models import VectorDocument

logger = logging.getLogger(__name__)


class VectorDatabaseService(BaseVectorProvider):
    """Service for managing vector database operations across different providers"""
    
    def __init__(self, vector_db_type: Optional[str] = None):
        """
        Initialize the vector database service
        
        Args:
            vector_db_type: Type of vector database to use. If None, will use VECTOR_DB_TYPE env var
        """
        
        from ..models import VectorIndexConfig
        
        # Create a config for the service itself
        config = VectorIndexConfig(
            name="vector_service",
            vector_db_type=vector_db_type or os.getenv("VECTOR_DB_TYPE", "weaviate").lower(),
            description="Vector database service abstraction layer",
            weaviate_url=os.getenv("WEAVIATE_URL", "http://localhost:8082"),
            weaviate_api_key=os.getenv("WEAVIATE_API_KEY"),
        )
        super().__init__(config)
        
        self.vector_db_type = self.config.vector_db_type
        self.provider: Optional[BaseVectorProvider] = None
    
    async def initialize(self) -> bool:
        """Initialize the vector database provider"""
        
        try:
            logger.info(f"Initializing vector database service with type: {self.vector_db_type}")
            self.provider = self._create_provider()
            
            if self.provider:
                logger.info(f"Created {self.vector_db_type} provider, initializing...")
                success = await self.provider.initialize()
                
                if success:
                    self._initialized = True
                    logger.info(f"Vector database service initialized with {self.vector_db_type} provider")
                else:
                    logger.error(f"Failed to initialize {self.vector_db_type} provider")
                return success
            else:
                logger.error(f"Failed to create {self.vector_db_type} provider")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize vector database service: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
    
    def _create_provider(self) -> Optional[BaseVectorProvider]:
        """Create the appropriate vector database provider based on configuration"""
        
        try:
            # Factory pattern for creating vector database providers
            provider_factory = {
                "chroma": self._create_chroma_provider,
                "chromadb": self._create_chroma_provider,  # Alias for chroma
            }
            
            print(f"🔍 [DEBUG] Available providers: {list(provider_factory.keys())}")
            
            if self.vector_db_type in provider_factory:
                provider = provider_factory[self.vector_db_type]()
                return provider
            else:
                logger.warning(f"Unsupported vector database type '{self.vector_db_type}', defaulting to ChromaDB")
                return self._create_chroma_provider()
        except Exception as e:
            logger.error(f"Failed to create vector database provider: {e}")
            return None
    
    
    def _create_chroma_provider(self) -> ChromaVectorProvider:
        """Create a ChromaDB provider instance"""
        chroma_host = os.getenv("CHROMA_HOST", "localhost")
        chroma_port = int(os.getenv("CHROMA_PORT", "8000"))
        collection_name = os.getenv("CHROMA_COLLECTION_NAME", "documents")
        
        return ChromaVectorProvider(
            host=chroma_host,
            port=chroma_port,
            collection_name=collection_name
        )
    
    async def store_embedding(self, chunks_with_embeddings: List[Dict[str, Any]], client_id: str, project_id: str) -> Dict[str, Any]:
        """
        Store a single set of chunks with embeddings in the vector database
        
        Args:
            chunks_with_embeddings: List of chunks with their embeddings
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            Dictionary containing the result of the storage operation
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            # Extract language from chunk metadata (if available)
            language = "en"  # Default
            if chunks_with_embeddings and len(chunks_with_embeddings) > 0:
                first_chunk = chunks_with_embeddings[0]
                language = first_chunk.get("metadata", {}).get("language", "en")
            
            # Update collection name to be scoped to language, client, and project
            if hasattr(self.provider, 'base_collection_name'):
                collection_name = f"chunks_{language}_{client_id}_{project_id}" if client_id and project_id else "documents"
                self.provider.base_collection_name = collection_name
                logger.info(f"Using ChromaDB collection: {collection_name}")
            
            result = await self.provider.store_embedding(chunks_with_embeddings, client_id, project_id)
            logger.info(f"Successfully stored {result.get('stored_chunks', 0)} chunks using {self.vector_db_type}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to store embedding in vector database: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "stored_chunks": 0,
                "successful_uuids": []
            }
    
    async def store_chunks(self, chunks: List[Dict[str, Any]], client_id: str, project_id: str) -> Dict[str, Any]:
        """
        Store document chunks in the vector database
        
        Deprecated: Use store_embedding instead. This method is kept for backward compatibility.
        
        Args:
            chunks: List of chunk dictionaries containing text and metadata
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            
        Returns:
            Dictionary containing the result of the storage operation
        """
        logger.warning("store_chunks is deprecated, use store_embedding instead")
        return await self.store_embedding(chunks, client_id, project_id)
    
    async def similarity_search(self, query: str, client_id: str, project_id: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Perform similarity search in the vector database
        
        Args:
            query: Search query string
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            top_k: Number of results to return
            
        Returns:
            List of similar documents
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            # Update collection name to match the same format used in store_chunks
            if hasattr(self.provider, 'base_collection_name'):
                collection_name = f"chunks_{client_id}_{project_id}" if client_id and project_id else "documents"
                # Update the provider's collection name if it supports it
                if hasattr(self.provider, 'base_collection_name'):
                    self.provider.base_collection_name = collection_name
            
            return await self.provider.similarity_search(query, client_id, project_id, top_k)
        except Exception as e:
            logger.error(f"Failed to perform similarity search: {e}")
            return []
    
    async def delete_chunks(self, client_id: str, project_id: str, object_name: str) -> Dict[str, Any]:
        """
        Delete chunks associated with a specific object
        
        Args:
            client_id: Client identifier for data isolation
            project_id: Project identifier for data isolation
            object_name: Name of the object whose chunks should be deleted
            
        Returns:
            Dictionary containing the result of the deletion operation
        """
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.delete_chunks(client_id, project_id, object_name)
        except Exception as e:
            logger.error(f"Failed to delete chunks: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "deleted_chunks": 0
            }
    
    async def health_check(self) -> Dict[str, Any]:
        """Check the health of the vector database service"""
        if not self._initialized or not self.provider:
            return {
                "status": "unhealthy",
                "error": "Service not initialized"
            }
        
        try:
            return await self.provider.health_check()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "error": str(e)
            }
    
    async def close(self):
        """Close the vector database connection"""
        if self.provider and hasattr(self.provider, 'close'):
            try:
                await self.provider.close()
                logger.info("Vector database connection closed")
            except Exception as e:
                logger.error(f"Error closing vector database connection: {e}")
        
        self._initialized = False
        self.provider = None
    
    def is_initialized(self) -> bool:
        """Check if the service is initialized"""
        return self._initialized and self.provider is not None
    
    def get_provider_type(self) -> str:
        """Get the type of vector database provider being used"""
        return self.vector_db_type
    
    # Additional methods from BaseVectorProvider interface
    
    async def create_index(self) -> bool:
        """Create a new vector index"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.create_index()
        except Exception as e:
            logger.error(f"Failed to create index: {e}")
            return False
    
    async def delete_index(self) -> bool:
        """Delete the vector index"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.delete_index()
        except Exception as e:
            logger.error(f"Failed to delete index: {e}")
            return False
    
    async def add_documents(self, documents: List[VectorDocument]) -> List[str]:
        """Add documents to the index"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.add_documents(documents)
        except Exception as e:
            logger.error(f"Failed to add documents: {e}")
            return []
    
    async def update_documents(self, documents: List[VectorDocument]) -> bool:
        """Update existing documents"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.update_documents(documents)
        except Exception as e:
            logger.error(f"Failed to update documents: {e}")
            return False
    
    async def delete_documents(self, document_ids: List[str]) -> bool:
        """Delete documents from the index"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.delete_documents(document_ids)
        except Exception as e:
            logger.error(f"Failed to delete documents: {e}")
            return False
    
    async def get_document(self, document_id: str) -> Optional[VectorDocument]:
        """Retrieve a specific document"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.get_document(document_id)
        except Exception as e:
            logger.error(f"Failed to get document: {e}")
            return None
    
    async def get_index_stats(self) -> Dict[str, Any]:
        """Get index statistics"""
        if not self._initialized or not self.provider:
            raise RuntimeError("Vector database service not initialized")
        
        try:
            return await self.provider.get_index_stats()
        except Exception as e:
            logger.error(f"Failed to get index stats: {e}")
            return {"error": str(e)}
