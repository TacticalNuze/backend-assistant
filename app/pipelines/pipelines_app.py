from typing import Any, Dict, Tuple, List, Optional
import json
import ast
import os
import logging
import time
import asyncio

from libs.llm_service.gateway import LLMGateway
from libs.promptStore_service import get_default_langfuse_prompt_manager
import openai
import json
from libs.database_service.storage import MinIOStorageManager
from libs.database_service.service import DatabaseService
from libs.database_service.store_results import get_store_results
from libs.embeddings_service import EmbeddingGeneratorInterface
from libs.memory_service.providers import Mem0Provider
from libs.database_service.sql_db.providers import PgSQLProvider
from libs.llm_service.utils import parse_llm_json_response, safe_literal_eval, flatten_dict
from libs.chunking_service.service import ChunkingGeneratorInterface
from libs.chunking_service.models import ChunkingConfig, ChunkingMethod

logger = logging.getLogger(__name__)


def get_input_hash(inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str) -> Tuple[str, str]:
    formatted_input_data = json.dumps(inputs, sort_keys=True)
    import hashlib
    input_hash = hashlib.sha256(formatted_input_data.encode()).hexdigest()
    return formatted_input_data, input_hash


class ParseDocuments:
    def __init__(self, inputs, project_name, prompt_config, pipeline_key):
        self.inputs = inputs

    def execute(self) -> List[Dict[str, Any]]:
        """Parse raw file data from GetFiles step into text content."""
        # Get raw files from GetFiles step
        raw_files = self.inputs.get("GetFiles", [])
        if not raw_files:
            # Fallback to legacy format
            raw_files = self.inputs.get("documents", [])
        
        parsed: List[Dict[str, Any]] = []
        
        for idx, file_data in enumerate(raw_files):
            try:
                if isinstance(file_data, dict):
                    raw_data = file_data.get("raw_data")
                    file_path = file_data.get("file_path", f"doc_{idx}.txt")
                    file_extension = file_data.get("file_extension", ".txt")
                    metadata = file_data.get("metadata", {})
                    
                    # Skip files that failed to retrieve
                    if raw_data is None and file_data.get("error"):
                        logger.warning(f"Skipping file {file_path} due to retrieval error: {file_data.get('error')}")
                        continue
                    
                    # Generate unique file ID based on file path
                    file_id = f"file_{idx}_{hash(file_path) % 10000}"
                    
                    # Parse based on file type
                    if file_extension == '.pdf' and isinstance(raw_data, bytes):
                        # Parse PDF content
                        import asyncio
                        text_content = asyncio.run(self._parse_pdf_content(raw_data, file_path))
                    elif isinstance(raw_data, str):
                        # Text content
                        text_content = raw_data
                    elif isinstance(raw_data, bytes):
                        # Try to decode bytes as text
                        try:
                            text_content = raw_data.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                text_content = raw_data.decode('latin-1')
                            except UnicodeDecodeError:
                                text_content = f"Binary content from {file_path} (could not decode as text)"
                    elif isinstance(raw_data, dict):
                        # JSON-like content
                        text_content = json.dumps(raw_data)
                    else:
                        # Fallback to string conversion
                        text_content = str(raw_data) if raw_data is not None else ""
                    
                    parsed.append({
                        "text": text_content,
                        "file_id": file_id,
                        "document_id": f"doc_{idx}",
                        "metadata": {
                            "file_id": file_id,
                            "file_path": file_path,
                            "file_extension": file_extension,
                            "file_name": os.path.basename(file_path),
                            "document_index": idx,
                            **metadata
                        }
                    })
                    logger.info(f"Parsed document {idx} (file_id: {file_id}): {file_path} ({len(text_content)} characters)")
                    
                else:
                    # Handle legacy format or plain strings
                    text_content = str(file_data)
                    file_id = f"file_{idx}_legacy"
                    parsed.append({
                        "text": text_content,
                        "file_id": file_id,
                        "document_id": f"doc_{idx}",
                        "metadata": {
                            "file_id": file_id,
                            "file_path": f"doc_{idx}.txt",
                            "document_index": idx
                        }
                    })
                    
            except Exception as e:
                logger.error(f"Error parsing document {idx}: {e}")
                # Add placeholder for failed parsing
                file_id = f"file_{idx}_error"
                parsed.append({
                    "text": f"Failed to parse document {idx}: {str(e)}",
                    "file_id": file_id,
                    "document_id": f"doc_{idx}",
                    "metadata": {
                        "file_id": file_id,
                        "file_path": file_data.get("file_path", f"doc_{idx}.txt") if isinstance(file_data, dict) else f"doc_{idx}.txt",
                        "document_index": idx,
                        "parse_error": str(e)
                    }
                })
        
        logger.info(f"Successfully parsed {len(parsed)} documents with file IDs")
        return parsed

    async def _parse_pdf_content(self, pdf_data: bytes, file_name: str) -> str:
        """Parse PDF content from binary data"""
        try:
            # Try to use PyPDF2 for PDF parsing
            import PyPDF2
            import io
            
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
            text_content = ""
            
            for page_num, page in enumerate(pdf_reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_content += page_text + "\n"
            
            if not text_content.strip():
                text_content = f"PDF content from {file_name} (text extraction failed)"
            
            logger.info(f"Extracted {len(text_content)} characters from PDF {file_name}")
            return text_content
            
        except ImportError:
            logger.warning("PyPDF2 not available, returning placeholder text")
            return f"PDF content from {file_name} (PyPDF2 not available)"
        except Exception as e:
            logger.error(f"Error parsing PDF {file_name}: {e}")
            return f"PDF content from {file_name} (parsing failed: {e})"


class GetFiles:
    def __init__(self, inputs, project_name, prompt_config, pipeline_key):
        self.inputs = inputs

    def execute(self) -> List[Dict[str, Any]]:
        """Retrieve raw file data from MinIO using client_id and project_id.
        
        This step only retrieves files without parsing or processing them.
        Parsing should be done in the parse_documents step.
        
        Supports input formats:
        - client_id/project_id structure: {"client_id": "testclient", "project_id": "testproject"}
        - Legacy bucket/key format: {"documents": [{"bucket": "...", "key": "..."}]}
        - Direct content: {"content": "...", "file_path": "name.txt"}
        """
        # Check for new client_id/project_id structure
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        
        if client_id and project_id:
            import asyncio
            return asyncio.run(self._get_files_by_project(client_id, project_id))
        
        # Fallback to legacy documents format
        docs = self.inputs.get("documents", [])
        if not isinstance(docs, list):
            docs = [docs]

        import asyncio
        
        async def _get_legacy_files():
            storage = MinIOStorageManager(
                endpoint=os.getenv("MINIO_ENDPOINT", "localhost:"),
                access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
                secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
                secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
            )
            await storage.initialize()

            results: List[Dict[str, Any]] = []
            for idx, item in enumerate(docs):
                if isinstance(item, dict) and item.get("bucket") and item.get("key"):
                    bucket = item["bucket"]
                    key = item["key"]
                    try:
                        # Retrieve raw file data without parsing
                        file_extension = os.path.splitext(key)[1].lower()
                        if file_extension == '.pdf':
                            # Retrieve PDF as binary data
                            data = await storage.retrieve_output(bucket, key, output_type="binary")
                        else:
                            # Retrieve text files as text
                            data = await storage.retrieve_output(bucket, key, output_type="text")
                        
                        results.append({
                            "raw_data": data,
                            "file_path": key,
                            "file_extension": file_extension,
                            "bucket": bucket,
                            "metadata": {
                                "file_path": item.get("file_path", key),
                                "bucket": bucket,
                                "original_key": key
                            }
                        })
                    except Exception as e:
                        logger.error(f"Failed to retrieve {key} from MinIO: {e}")
                        # Add placeholder for failed retrieval
                        results.append({
                            "raw_data": None,
                            "file_path": key,
                            "file_extension": os.path.splitext(key)[1].lower(),
                            "bucket": bucket,
                            "error": str(e),
                            "metadata": {
                                "file_path": item.get("file_path", key),
                                "bucket": bucket,
                                "original_key": key,
                                "error": str(e)
                            }
                        })
                elif isinstance(item, dict) and (item.get("content") or item.get("text")):
                    # Direct content provided
                    content = item.get("content") or item.get("text") or ""
                    file_path = item.get("file_path", f"doc_{idx}.txt")
                    results.append({
                        "raw_data": content,
                        "file_path": file_path,
                        "file_extension": os.path.splitext(file_path)[1].lower() or ".txt",
                        "bucket": None,
                        "metadata": {
                            "file_path": file_path,
                            "direct_content": True
                        }
                    })
                else:
                    # Treat as plain string
                    content = str(item)
                    file_path = f"doc_{idx}.txt"
                    results.append({
                        "raw_data": content,
                        "file_path": file_path,
                        "file_extension": ".txt",
                        "bucket": None,
                        "metadata": {
                            "file_path": file_path,
                            "plain_string": True
                        }
                    })

            logger.info(f"Retrieved {len(results)} raw files")
            return results
        
        return asyncio.run(_get_legacy_files())


    async def _get_files_by_project(self, client_id: str, project_id: str) -> List[Dict[str, Any]]:
        """Retrieve all raw files from a project in MinIO without parsing."""
        storage = MinIOStorageManager(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        )
        await storage.initialize()

        results: List[Dict[str, Any]] = []
        prefix = f"{project_id}/"
        
        logger.info(f"🔍 Retrieving files from MinIO bucket: {client_id}, prefix: {prefix}")
        
        try:
            # List all objects in the client_id/project_id/ prefix
            objects = await storage.list_objects(client_id, prefix=prefix)
            
            logger.info(f"📁 Found {len(objects)} objects in {client_id}/{project_id}")
            
            # Filter for supported file types
            supported_extensions = {'.pdf', '.txt', '.md', '.doc', '.docx', '.json'}
            
            for idx, obj in enumerate(objects):
                object_key = obj.object_name
                file_name = os.path.basename(object_key)
                file_extension = os.path.splitext(object_key)[1].lower()
                
                # Skip unsupported file types
                if file_extension not in supported_extensions:
                    logger.info(f"⏭️ Skipping unsupported file type: {object_key} ({file_extension})")
                    continue
                
                logger.info(f"📄 Processing file {idx + 1}: {object_key} ({file_extension})")
                
                try:
                    # Retrieve raw file data without parsing
                    if file_extension == '.pdf':
                        # Retrieve PDF as binary data
                        data = await storage.retrieve_output(client_id, object_key, output_type="binary")
                        logger.info(f"✅ Retrieved PDF file: {object_key} ({len(data)} bytes)")
                    else:
                        # Retrieve text files as text
                        data = await storage.retrieve_output(client_id, object_key, output_type="text")
                        logger.info(f"✅ Retrieved text file: {object_key} ({len(data)} characters)")
                    
                    results.append({
                        "raw_data": data,
                        "file_path": object_key,
                        "file_extension": file_extension,
                        "bucket": client_id,
                        "metadata": {
                            "file_path": object_key,
                            "bucket": client_id,
                            "project_id": project_id,
                            "file_name": file_name,
                            "file_size": len(data) if isinstance(data, (str, bytes)) else 0,
                            "file_type": file_extension
                        }
                    })
                    
                except Exception as e:
                    logger.error(f"❌ Failed to retrieve {object_key} from MinIO: {e}")
                    # Add placeholder for failed retrieval
                    results.append({
                        "raw_data": None,
                        "file_path": object_key,
                        "file_extension": file_extension,
                        "bucket": client_id,
                        "error": str(e),
                        "metadata": {
                            "file_path": object_key,
                            "bucket": client_id,
                            "project_id": project_id,
                            "file_name": file_name,
                            "error": str(e)
                        }
                    })
                    
        except Exception as e:
            logger.error(f"❌ Failed to list objects in bucket {client_id} with prefix {prefix}: {e}")
        
        logger.info(f"🎉 Successfully retrieved {len(results)} files from {client_id}/{project_id}")
        
        # Log summary of file types
        file_types = {}
        for result in results:
            ext = result.get("file_extension", "unknown")
            file_types[ext] = file_types.get(ext, 0) + 1
        
        logger.info(f"📊 File type summary: {file_types}")
        
        return results

class ChunkDocuments:
    """Split documents into smaller chunks for processing"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config: Dict[str, Any], pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config = prompt_config
        self.pipeline_key = pipeline_key

    def execute(self) -> List[Dict[str, Any]]:
        """Split parsed documents into chunks with consistent SHA256-based chunk_ids"""
        parsed_documents = self.inputs.get("parse_documents", [])
        
        if not parsed_documents:
            logger.error("No parsed documents found for chunking")
            return []
        
        logger.info(f"Chunking {len(parsed_documents)} documents")
        
        # Convert to DocumentChunk format for processing
        from libs.preprocessing_service.models import DocumentChunk, DocumentMetadata, DocumentFormat
        import hashlib
        
        # Get client_id, project_id, and language for consistent hashing
        client_id = self.inputs.get("client_id", "default")
        project_id = self.inputs.get("project_id", "default")
        language = self.inputs.get("language", "en")
        
        chunks = []
        for i, doc in enumerate(parsed_documents):
            if isinstance(doc, dict):
                content = doc.get("text", "")
                file_id = doc.get("file_id", f"file_{i}")
                document_id = doc.get("document_id", f"doc_{i}")
                file_path = doc.get("metadata", {}).get("file_path")
                file_name = doc.get("metadata", {}).get("file_name")
                file_extension = doc.get("metadata", {}).get("file_extension", ".txt")
                
                # Use file_name as object_name for consistency with ChromaDB
                object_name = file_name or file_path or f"doc_{i}"
                
                # Simple chunking - split by paragraphs or sentences
                # In a real implementation, you'd use more sophisticated chunking
                chunk_size = 1000  # characters
                text_chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
                
                for chunk_idx, chunk_text in enumerate(text_chunks):
                    if chunk_text.strip():  # Skip empty chunks
                        # Generate deterministic SHA256 hash-based chunk_id
                        # This MUST match the logic in ChromaDB storage
                        # Include language for multi-language support
                        raw_id = f"{language}_{client_id}_{project_id}_{object_name}_{chunk_text}"
                        chunk_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
                        
                        metadata = DocumentMetadata(
                            file_name=file_name,
                            file_path=file_path,
                            file_size=len(chunk_text.encode('utf-8')),
                            format=DocumentFormat.TXT
                        )
                        
                        document_chunk = DocumentChunk(
                            chunk_id=chunk_id,
                            text=chunk_text,
                            metadata=metadata,
                            chunk_index=chunk_idx,
                            start_char=chunk_idx * chunk_size,
                            end_char=min((chunk_idx + 1) * chunk_size, len(content))
                        )
                        
                        chunks.append({
                            "chunk_id": chunk_id,  # SHA256 hash
                            "file_id": file_id,
                            "document_id": document_id,
                            "text": chunk_text,
                            "object_name": object_name,  # Add object_name for consistency
                            "metadata": {
                                "file_id": file_id,
                                "file_path": file_path,
                                "file_name": file_name,
                                "object_name": object_name,
                                "file_extension": file_extension,
                                "chunk_index": chunk_idx,
                                "parent_doc_id": document_id,
                                "parent_file_id": file_id,
                                "client_id": client_id,
                                "project_id": project_id,
                                "language": language
                            },
                            "document_chunk": document_chunk.model_dump(mode='json')  # Serialize to dict with JSON-compatible types
                        })
        
        logger.info(f"Created {len(chunks)} chunks from {len(parsed_documents)} documents with SHA256 chunk_ids")
        if chunks:
            logger.info(f"Sample chunk_id: {chunks[0]['chunk_id']}")
        return chunks

class UploadToObjectStorage:
    """Upload file content to object storage (MinIO)"""
    
    def __init__(self, inputs, project_name, prompt_config_src, pipeline_key):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        """Execute the full Vector RAG pipeline using the orchestrator"""
        logger.info("🚀 Starting Full Preprocessing Pipeline Execution")
        
        # Run the async execution in a new event loop
        import asyncio
        return asyncio.run(self._execute_async())
    
    async def _execute_async(self) -> Dict[str, Any]:
        """Upload file to object storage and return object name"""
        try:
            from libs.database_service.service import DatabaseService
            import base64
            
            client_id = self.inputs.get("client_id")
            project_id = self.inputs.get("project_id")
            file_content = self.inputs.get("file_content")
            filename = self.inputs.get("filename")
            content_type = self.inputs.get("content_type", "application/octet-stream")
            
            if not all([client_id, project_id, file_content, filename]):
                raise ValueError("Missing required inputs: client_id, project_id, file_content, filename")
            
            # Handle base64 encoded file content
            if isinstance(file_content, str):
                try:
                    # Try to decode as base64 first
                    file_content = base64.b64decode(file_content)
                except Exception:
                    # If base64 decoding fails, encode as utf-8 bytes
                    file_content = file_content.encode('utf-8')
            elif not isinstance(file_content, bytes):
                # Convert to bytes if it's not already
                file_content = str(file_content).encode('utf-8')
            
            # Initialize database service
            db_service = DatabaseService()
            await db_service.initialize()
            
            # Upload file using database service
            object_name = await db_service.upload_file(
                file_data=file_content,
                filename=filename,
                client_id=client_id,
                project_id=project_id,
                content_type=content_type
            )
            
            logger.info(f"Uploaded file {filename} to object storage: {object_name}")
            
            return {
                "object_name": object_name,
                "filename": filename,
                "client_id": client_id,
                "project_id": project_id,
                "content_type": content_type,
                "file_size": len(file_content),
                "upload_timestamp": time.time()
            }
            
        except Exception as e:
            logger.error(f"Error uploading to object storage: {e}")
            raise


class ParseDocumentToMarkdown:
    """Parse uploaded document to markdown format - Celery-compatible version"""
    
    def __init__(self, inputs, project_name, prompt_config_src, pipeline_key):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        """Parse document to markdown using synchronous LlamaCloud API"""
        logger.info("🚀 Starting Document Parsing to Markdown")
        
        try:
            import tempfile
            import os
            import base64
            import time
            import requests
            import json
            
            # Get files result from previous step (GetFiles)
            files_result = self.inputs.get("get_files", [])
            if not files_result:
                raise ValueError("No files found from GetFiles step")
            
            # Get the first file from the results
            file_data = files_result[0] if isinstance(files_result, list) else files_result
            filename = file_data.get("file_path", "unknown")
            file_extension = file_data.get("file_extension", ".txt")
            raw_data = file_data.get("raw_data")
            
            if not raw_data:
                raise ValueError("No file content found in GetFiles result")
            
            # Use raw_data as file content
            file_content = raw_data
            
            # Handle base64 encoded file content
            if isinstance(file_content, str):
                try:
                    # Try to decode as base64 first
                    file_content = base64.b64decode(file_content)
                except Exception:
                    # If base64 decoding fails, encode as utf-8 bytes
                    file_content = file_content.encode('utf-8')
            elif not isinstance(file_content, bytes):
                # Convert to bytes if it's not already
                file_content = str(file_content).encode('utf-8')
            
            # Create temporary file for parsing
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
                tmp_file.write(file_content)
                tmp_file_path = tmp_file.name
            
            try:
                # Use synchronous LlamaCloud parsing adapter
                from libs.parsing_service.service import create_sync_parsing_adapter
                
                api_key = os.getenv("LLAMA_CLOUD_API_KEY")
                base_url = os.getenv("LLAMA_CLOUD_BASE_URL", "https://api.cloud.llamaindex.ai")
                
                if not api_key:
                    raise ValueError("LLAMA_CLOUD_API_KEY environment variable is required")
                
                # Create synchronous parsing adapter
                parsing_adapter = create_sync_parsing_adapter(
                    api_key=api_key,
                    base_url=base_url,
                    verify_ssl=False
                )
                
                # Parse document to markdown
                result = parsing_adapter.parse_document_to_markdown(tmp_file_path)
                markdown_content = result.content
                
                logger.info(f"Parsed document {filename} to markdown ({len(markdown_content)} characters)")
                
                return {
                    "markdown_content": markdown_content,
                    "filename": filename,
                    "content_type": f"application/{file_extension[1:]}" if file_extension else "application/octet-stream",
                    "file_size": len(file_content),
                    "parsed_size": len(markdown_content),
                    "get_files_result": file_data
                }
                
            finally:
                # Clean up temporary file
                if os.path.exists(tmp_file_path):
                    os.remove(tmp_file_path)
                    
        except Exception as e:
            logger.error(f"Error parsing document to markdown: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise



class ChunkDocument:
    """Chunk parsed document for RAG processing using Chonkie chunkers"""
    
    def __init__(self, inputs, project_name, prompt_config_src, pipeline_key):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        """Chunk document for RAG processing"""
        try:
            
            # Get parse result from previous step
            parse_result = self.inputs.get("parse_document", {})
            
            # Get chunking parameters from initial inputs (always available)
            chunk_size = self.inputs.get("chunk_size", 1000)
            chunk_overlap = self.inputs.get("chunk_overlap", 200)
            
            markdown_content = parse_result.get("markdown_content", "")
            filename = parse_result.get("filename", "unknown")
            
            if not markdown_content:
                logger.info(f"No content to chunk for {filename}")
                return {
                    "chunks": [],
                    "chunking_metadata": {
                        "total_chunks": 0,
                        "chunk_size": chunk_size,
                        "chunk_overlap": chunk_overlap,
                        "chunking_method": "none"
                    },
                    "parse_result": parse_result
                }
                        
            # Create document metadata
            document_metadata = {
                "filename": filename,
                "size": len(markdown_content),
                "content_type": parse_result.get("content_type", "text/plain")
            }
            
            # Get chunking method from inputs (default to token_chunker)
            logger.info("⚙️  Getting chunking parameters from inputs...")
            chunking_method = self.inputs.get("chunking_method", "token_chunker")
            embeddings_provider = self.inputs.get("embedding_provider", "azure_openai")
            embeddings_model = self.inputs.get("embedding_model", "text-embedding-3-large")
            
            logger.info(f"📋 Chunking configuration:")
            logger.info(f"  - Method: {chunking_method}")
            logger.info(f"  - Provider: {embeddings_provider}")
            logger.info(f"  - Model: {embeddings_model}")
            logger.info(f"  - Chunk size: {chunk_size}")
            logger.info(f"  - Chunk overlap: {chunk_overlap}")
            logger.info(f"  - Content size: {len(markdown_content)} characters")
            
            # Create chunking config
            logger.info("🔧 Creating ChunkingConfig object...")
            chunking_config = ChunkingConfig(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                method=ChunkingMethod(chunking_method),
                embeddings_provider=embeddings_provider,
                embeddings_model=embeddings_model
            )
            logger.info(f"✅ ChunkingConfig created successfully")
            
            # Use the chunking service interface
            logger.info("🏭 Creating ChunkingGeneratorInterface...")
            chunking_service = ChunkingGeneratorInterface()
            logger.info("✅ ChunkingGeneratorInterface created successfully")
            
            # Chunk synchronously
            rag_chunks = chunking_service.chunk_document_for_rag_sync(
                text=markdown_content,
                config=chunking_config,
                provider=chunking_method,
                document_metadata=document_metadata
            )
            
            logger.info(f"Created {rag_chunks.chunking_metadata['total_chunks']} chunks for {filename}")
            
            # Convert DocumentChunk objects to dictionaries for serialization
            chunks_data = []
            for chunk in rag_chunks.chunks:
                chunks_data.append({
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "metadata": {
                        "chunk_index": chunk.metadata.chunk_index,
                        "chunk_size": chunk.metadata.chunk_size,
                        "chunk_type": chunk.metadata.chunk_type.value,
                        "chunking_method": chunk.metadata.chunking_method.value,
                        "provider": chunk.metadata.provider,
                        "document_filename": chunk.metadata.document_filename,
                        "document_size": chunk.metadata.document_size,
                        "source_document_name": chunk.metadata.source_document_name,
                        "custom_metadata": chunk.metadata.custom_metadata
                    }
                })
            
            return {
                "chunks": chunks_data,
                "chunking_metadata": rag_chunks.chunking_metadata,
                "parse_result": parse_result
            }
            
        except Exception as e:
            logger.error(f"Error chunking document: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise


class GenerateChunkEmbeddings:
    """Generate embeddings for document chunks"""
    
    def __init__(self, inputs, project_name, prompt_config_src, pipeline_key):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        """Generate embeddings for document chunks"""
        try:
            import asyncio
            from libs.embeddings_service import EmbeddingGeneratorInterface
            
            # Handle both parallel task (fan-out) and normal execution
            # In fan-out mode, chunk_documents is distributed by Celery and each child receives:
            # - Either a single chunk dict directly, OR
            # - The full chunk_documents result with "chunks" key
            chunks_input = self.inputs.get("chunk_documents")
            if chunks_input is None:
                # Fallback to legacy single chunk_document step
                chunks_input = self.inputs.get("chunk_document")
            
            # Normalize chunks_input to always be a list of chunk dicts
            if isinstance(chunks_input, list):
                # Already a list of chunks (from parallel task distribution or multiple documents)
                chunks = chunks_input
            elif isinstance(chunks_input, dict):
                # Could be either:
                # 1. ChunkDocument step result with "chunks" key: {"chunks": [...]}
                # 2. Single chunk dict from fan-out: {"chunk_id": ..., "text": ..., ...}
                if "chunks" in chunks_input and isinstance(chunks_input["chunks"], list):
                    # Case 1: Full result from ChunkDocument step
                    chunks = chunks_input["chunks"]
                else:
                    # Case 2: Single chunk from fan-out distribution
                    chunks = [chunks_input]
            else:
                chunks = []
            
            # Preserve original input for debugging
            chunk_result = chunks_input if isinstance(chunks_input, dict) else {"chunks": chunks}
            
            if not chunks:
                logger.info("No chunks to generate embeddings for")
                return {
                    "chunks_with_embeddings": [],
                    "embedding_metadata": {
                        "total_chunks": 0,
                        "embedding_model": "none",
                        "embedding_dimension": 0,
                        "processing_time": 0.0
                    },
                    "chunk_result": chunk_result
                }
            
            # Get embedding configuration from inputs or use defaults
            embedding_model = self.inputs.get("embedding_model", "text-embedding-3-large")
            embedding_provider = self.inputs.get("embedding_provider", "azure_openai")
            batch_size = self.inputs.get("embedding_batch_size", 100)  # Larger batch size for OpenAI
            
            # Create embedding service
            embedding_service = EmbeddingGeneratorInterface(default_provider=embedding_provider)
            generator = embedding_service.get_generator(
                embedding_provider,
                model_name=embedding_model,
                batch_size=batch_size,
            )
            
            # Extract texts from chunks
            texts = [chunk["text"] for chunk in chunks]
            
            # Generate embeddings using async method 
            async def generate_embeddings():
                return await generator.generate_embeddings_batch(
                    texts,
                    batch_size=batch_size,
                )
            
            # Run the async embedding generation
            embeddings = asyncio.run(generate_embeddings())
            
            # Combine chunks with their embeddings and preserve file_name mapping
            chunks_with_embeddings = []
            file_chunk_mapping = {}  # Map file_name -> list of (chunk_id, embedding)
            
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_with_embedding = chunk.copy()
                chunk_with_embedding["embedding"] = embedding
                
                # Extract file_name from chunk metadata
                file_name = chunk.get("metadata", {}).get("file_name", "unknown")
                chunk_id = chunk.get("chunk_id", f"chunk_{i}")
                
                chunk_with_embedding["embedding_metadata"] = {
                    "model": embedding_model,
                    "provider": embedding_provider,
                    "dimension": len(embedding),
                    "chunk_index": i,
                    "file_name": file_name  # Include file_name in embedding metadata
                }
                chunks_with_embeddings.append(chunk_with_embedding)
                
                # Build file_name to chunks/embeddings mapping
                if file_name not in file_chunk_mapping:
                    file_chunk_mapping[file_name] = []
                file_chunk_mapping[file_name].append({
                    "chunk_id": chunk_id,
                    "chunk_index": i,
                    "text": chunk.get("text", ""),
                    "embedding_dimension": len(embedding)
                })
            
            logger.info(f"Generated embeddings for {len(chunks_with_embeddings)} chunks using {embedding_model}")
            logger.info(f"File mapping: {len(file_chunk_mapping)} unique files with chunks")
            for file_name, chunk_list in file_chunk_mapping.items():
                logger.info(f"  - {file_name}: {len(chunk_list)} chunks")
            
            if chunks_with_embeddings:
                logger.info(f"Sample chunk with embedding: {chunks_with_embeddings[0].get('chunk_id', 'unknown')}")
                logger.debug(f"First chunk keys: {list(chunks_with_embeddings[0].keys())}")
                logger.debug(f"Has 'text' key: {'text' in chunks_with_embeddings[0]}")
                logger.debug(f"Has 'embedding' key: {'embedding' in chunks_with_embeddings[0]}")
                logger.debug(f"Embedding dimension: {len(chunks_with_embeddings[0].get('embedding', []))}")
            
            return {
                "chunks_with_embeddings": chunks_with_embeddings,
                "file_chunk_mapping": file_chunk_mapping,
                "embedding_metadata": {
                    "total_chunks": len(chunks_with_embeddings),
                    "total_files": len(file_chunk_mapping),
                    "embedding_model": embedding_model,
                    "embedding_provider": embedding_provider,
                    "embedding_dimension": len(embeddings[0]) if embeddings else 0,
                    "processing_time": 0.0  # Could be calculated if needed
                },
                "chunk_result": chunk_result
            }
            
        except Exception as e:
            logger.error(f"Error generating chunk embeddings: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "chunks_with_embeddings": [],
                "embedding_metadata": {
                    "total_chunks": 0,
                    "embedding_model": "error",
                    "embedding_dimension": 0,
                    "processing_time": 0.0,
                    "error": str(e)
                },
                "chunk_result": self.inputs.get("chunk_document", {})
            }
class StoreChunksInVectorDB:
    """Store document chunks in vector database"""
    
    def __init__(self, inputs, project_name, prompt_config_src, pipeline_key):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        """Execute the store chunks operation synchronously"""

        import asyncio
        
        try:
            # Try to get the current event loop
            loop = asyncio.get_running_loop()

            import concurrent.futures
            import threading
            
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(self._execute_async())
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                result = future.result()
                return result
                
        except RuntimeError:
            # No event loop is running, we can use asyncio.run()
            result = asyncio.run(self._execute_async())
            return result

    async def _execute_async(self) -> Dict[str, Any]:
        """Store chunks in vector database"""
        
        try:
            from libs.database_service.service import DatabaseService
            
            # Get embedding result from previous step
            generate_emebedding_result = self.inputs.get("generate_embeddings", {})
            
            # Get client and project info directly from inputs
            client_id = self.inputs.get("client_id")
            project_id = self.inputs.get("project_id")

            logger.info(f"Generate embedding result keys: {list(generate_emebedding_result.keys())}")
            logger.info(f"Generate embedding result type: {type(generate_emebedding_result)}")
            
            # Get chunks with embeddings - Celery handles distribution, so this is a single embedding
            chunks_with_embeddings = generate_emebedding_result.get("chunks_with_embeddings", [])
            logger.info(f"Extracted chunks_with_embeddings: {chunks_with_embeddings} chunks")
            if chunks_with_embeddings:
                logger.info(f"Sample chunk keys: {list(chunks_with_embeddings[0].keys())}")
                logger.info(f"Has embedding: {'embedding' in chunks_with_embeddings[0]}")
                logger.info(f"Sample text: {chunks_with_embeddings[0].get('text', '')[:100]}")
#            if not chunks_with_embeddings:
#                logger.info("No chunks to store in vector database")
#                return {
#                    "status": "failed",
#                    "stored_chunks": 0,
#                    "successful_uuids": [],
#                    "reason": "no_chunks_in_inputs",
#                    "chunk_result": generate_emebedding_result
#                }
            
            # Initialize database service
            db_service = DatabaseService()
            await db_service.initialize()
            
            # Store this single embedding set
            vectorization_result = await db_service.store_embedding(
                chunks_with_embeddings=chunks_with_embeddings,
                client_id=client_id,
                project_id=project_id
            )
            
            logger.info(f"Stored {vectorization_result.get('stored_chunks', 0)} chunks in vector database")
            
            # Close the service connection
            await db_service.close()
            
            result = {
                "status": "success",
                "embedding_id": generate_emebedding_result.get("embedding_id", ""),
                "stored_chunks": vectorization_result.get("stored_chunks", 0),
                "successful_uuids": vectorization_result.get("successful_uuids", []),
                "vector_count": vectorization_result.get("stored_chunks", 0),
                "chunk_result": generate_emebedding_result
            }
            return result
            
        except Exception as e:
            logger.error(f"Error storing chunks in vector database: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "status": "failed",
                "error": str(e),
                "stored_chunks": 0,
                "successful_uuids": [],
                "chunk_result": self.inputs.get("generate_embeddings", {})
            }


class SaveMappingToDocumentDB:
    """Save per-file chunk embedding mappings into Elasticsearch document DB."""

    def __init__(self, inputs, project_name, prompt_config_src, pipeline_key):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        import asyncio
        return asyncio.run(self._execute_async())

    async def _execute_async(self) -> Dict[str, Any]:
        try:
            from libs.database_service.doc_db import ElasticsearchDocProvider

            gen_result = self.inputs.get("generate_embeddings", {})

            # Normalize to a flat list of chunks with embeddings
            all_chunks_with_embeddings: List[Dict[str, Any]] = []
            if isinstance(gen_result, list):
                for item in gen_result:
                    if isinstance(item, dict):
                        all_chunks_with_embeddings.extend(item.get("chunks_with_embeddings", []) or [])
            elif isinstance(gen_result, dict):
                all_chunks_with_embeddings = gen_result.get("chunks_with_embeddings", []) or []

            if not all_chunks_with_embeddings:
                logger.info("No chunks_with_embeddings found; nothing to save to document DB")
                return {"status": "failed", "error": "No chunks_with_embeddings found"}

            # Group by file_name and shape to provider input
            per_file_chunks: Dict[str, List[Dict[str, Any]]] = {}
            for chunk in all_chunks_with_embeddings:
                metadata = chunk.get("embedding_metadata", {}) or {}
                file_name = metadata.get("file_name") or chunk.get("metadata", {}).get("file_name") or "unknown"
                chunk_id = chunk.get("chunk_id")
                embedding = chunk.get("embedding")
                if chunk_id is None or embedding is None:
                    continue
                per_file_chunks.setdefault(file_name, []).append({
                    "chunk_id": chunk_id,
                    "embedding": embedding,
                })

            if not per_file_chunks:
                logger.info("No valid per-file chunks to save")
                return {"status": "failed", "error": "No valid per-file chunks to save"}

            client_id = self.inputs.get("client_id")
            project_id = self.inputs.get("project_id")
            language = self.inputs.get("language", "en")

            # Reasonable default index naming; include language, client, and project
            base_index = "chunk-embeddings"
            if client_id and project_id:
                index_name = f"{base_index}-{language}-{client_id}-{project_id}"
            else:
                index_name = base_index

            provider = ElasticsearchDocProvider()
            ok = await provider.initialize()
            if not ok:
                raise RuntimeError("Failed to initialize Elasticsearch document provider")

            per_file_results: Dict[str, Any] = {}
            indexed_total = 0
            for file_name, chunks in per_file_chunks.items():
                resp = await provider.save_chunk_embedding_mapping_to_document_db(
                    index_name=index_name,
                    file_name=file_name,
                    chunks=chunks,
                    client_id=client_id,
                    project_id=project_id,
                )
                per_file_results[file_name] = resp
                indexed_total += int(resp.get("indexed", 0))

            logger.info(f"Saved chunk embedding mappings to ES index={index_name} for {len(per_file_chunks)} files. Total indexed={indexed_total}")

            return {
                "status": "success",
                "index_name": index_name,
                "indexed_total": indexed_total,
                "per_file": per_file_results,
            }

        except Exception as e:
            logger.error(f"Error saving chunk embeddings mapping to document DB: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise


class ExtractUserFacts:
    def __init__(self, inputs: Dict[str, Any], project_name: str,
                 prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs

    def execute(self) -> Dict[str, Any]:
        logger.info('########################## ExtractUserFacts ##########################')
        logger.info(f'{self.inputs=}')

        input_text = self.inputs.get('input_text')
        client_id = self.inputs.get('client_id')
        project_id = self.inputs.get('project_id')
        session_id = self.inputs.get('session_id')
        role = 'user'  # role is only relevant for facts, not chat history

        provider = Mem0Provider()
        provider.configure()
        response = provider.create_memory(
            messages=[{"role": role, "content": input_text}],
            user_id=client_id,
            agent_id=project_id,
            run_id=session_id
        )

        logger.info('successfully extracted user facts')
        return response



class FetchUserFacts:
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs

    def execute(self) -> Dict[str, Any]:
        logger.info('########################## FetchUserFacts ##########################')
        logger.info(f'{self.inputs=}')

        input_text = self.inputs.get('input_text')
        client_id = self.inputs.get('client_id')
        project_id = self.inputs.get('project_id')
        session_id = self.inputs.get('session_id')

        provider = Mem0Provider()
        provider.configure()  # now sync

        # Search similar memories for this user
        response = provider.search_memories(
            query=input_text,
            user_id=client_id,
            agent_id=project_id,
            run_id=session_id
        )

        return response

class Save2ChatHistory:
    def __init__(self, inputs: Dict[str, Any], project_name: str,
                 prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.content = self.get_content()

    def get_role(self) -> str:
        """
        Subclasses override this to enforce the role.
        Default: read from inputs['role'] (fallback 'user').
        """
        return self.inputs.get('role', 'user')

    def get_content(self) -> str:
        """
        Subclasses override this to decide what content to save.
        Default: look for 'content' in inputs.
        """
        return self.inputs.get('content')

    def get_references(self) -> str:    
        return self.inputs.get('references', '["EmPtY!!"]')


    def execute(self) -> Dict[str, Any]:
        logger.info('########################## Save2ChatHistory ##########################')
        logger.info(f'{self.inputs=}')

        client_id = self.inputs.get('client_id')
        project_id = self.inputs.get('project_id')
        session_id = self.inputs.get('session_id')
        user_id = self.inputs.get('user_id') 
        role = self.get_role()
        references=self.get_references()

        db = PgSQLProvider()
        msg_id = db.store_message(
            client_id=client_id,
            project_id=project_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
            content=self.content, 
            references=references
        )

        logger.info(f'####################################\n########################{msg_id=}')
        logger.info(f'{references=}')

        logger.info(f'successfully stored {role} message with id {msg_id}')
        return {
            "message_id": msg_id,
            "role": role,
            "content": self.content, 
            "references": references
        }


class SaveUserMessage(Save2ChatHistory):
    def get_role(self) -> str:
        return "user"

    def get_content(self) -> str:
        return self.inputs.get("input_text")

    def get_references(self) -> str:
        return '[]'
    
class SaveVectorLLMMessage(Save2ChatHistory):
    def get_role(self) -> str:
        return "assistant"

    def get_content(self) -> str:
        return self.inputs.get("run_vector_rag")
    
    def get_references(self) -> str:
        return self.inputs.get('GetVectorReference', []).get('references', '["EmPtY!!"]')

class SearchRelevantChunks:
    """Search for relevant chunks using ChromaDB's built-in similarity search with custom embeddings"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs

    def execute(self) -> Dict[str, Any]:
        """Search for relevant chunks using ChromaDB's built-in similarity search, optionally including embeddings"""
        logger.info('########################## SearchRelevantChunks ##########################')
        logger.info(f'{self.inputs=}')

        input_text = self.inputs.get('input_text')
        client_id = self.inputs.get('client_id')
        project_id = self.inputs.get('project_id')
        top_k = self.inputs.get('top_k', 5)  # Default to 5 most relevant chunks
        include_embeddings = self.inputs.get('enable_diversification', False)  # Include embeddings only if diversification is enabled
        
        if not all([input_text, client_id, project_id]):
            logger.warning("Missing required inputs for chunk search")
            return {
                "relevant_chunks": [],
                "search_metadata": {
                    "total_chunks": 0,
                    "top_k": top_k,
                    "search_time": 0.0
                }
            }

        try:
            import asyncio
            import time
            from libs.database_service.service import DatabaseService
            
            start_time = time.time()
            
            # Get embedding configuration (should match preprocessing pipeline)
            embedding_model = self.inputs.get('embedding_model', 'text-embedding-3-large')
            embedding_provider = self.inputs.get('embedding_provider', 'azure_openai')
            
            logger.info(f"Using embedding model: {embedding_model} with provider: {embedding_provider}")
            
            # Use the DatabaseService for consistency
            db_service = DatabaseService()
            asyncio.run(db_service.initialize())
            
            # Get the ChromaDB provider
            chroma_provider = db_service.vector_manager.provider
            
            # Get language from inputs
            language = self.inputs.get('language', 'en')
            
            # Set the collection name to match the same format used in store_chunks
            chroma_provider.base_collection_name = f"chunks_{language}_{client_id}_{project_id}"
            logger.info(f"Searching in ChromaDB collection: chunks_{language}_{client_id}_{project_id}")
            
            # Use ChromaDB's built-in similarity search with custom embeddings
            relevant_chunks = asyncio.run(
                chroma_provider.similarity_search_with_custom_embeddings(
                    query_text=input_text,
                    client_id=client_id,
                    project_id=project_id,
                    embedding_model=embedding_model,
                    embedding_provider=embedding_provider,
                    top_k=top_k
                )
            )
            
            search_time = time.time() - start_time
            
            logger.info(f"Found {len(relevant_chunks)} relevant chunks using ChromaDB search")
            
            # DEBUG: Print all retrieved chunks
            logger.info("=" * 80)
            logger.info("DEBUG: RETRIEVED CHUNKS")
            logger.info("=" * 80)
            for i, chunk in enumerate(relevant_chunks, 1):
                logger.info(f"Chunk {i}:")
                logger.info(f"  Similarity: {chunk.get('similarity', 0):.4f}")
                logger.info(f"  Text: {chunk.get('text', '')[:200]}...")
                logger.info(f"  Metadata: {chunk.get('metadata', {})}")
                logger.info("-" * 40)
            
            # Log sample of results for debugging
            if relevant_chunks:
                sample_chunk = relevant_chunks[0]
                logger.info(f"Sample chunk (similarity: {sample_chunk.get('similarity', 0):.4f}): {sample_chunk.get('text', '')[:100]}...")
            
            # Close the database service connection
            asyncio.run(db_service.close())
            
            return {
                "relevant_chunks": relevant_chunks,
                "search_metadata": {
                    "total_chunks": len(relevant_chunks),
                    "top_k": top_k,
                    "search_time": search_time,
                    "client_id": client_id,
                    "project_id": project_id,
                    "embedding_model": embedding_model,
                    "embedding_provider": embedding_provider,
                    "search_method": "chromadb_builtin"
                }
            }
            
        except Exception as e:
            logger.error(f"Error searching relevant chunks: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Close the database service connection in case of error
            try:
                asyncio.run(db_service.close())
            except:
                pass  # Ignore errors when closing
            
            return {
                "relevant_chunks": [],
                "search_metadata": {
                    "total_chunks": 0,
                    "top_k": top_k,
                    "search_time": 0.0,
                    "error": str(e)
                }
            }


class GetVectorReference:
    """
    Resolve chunk_ids to filenames using ChromaDB metadata (primary) or Elasticsearch (fallback).
    
    With unified SHA256 chunk_ids, this step is now much simpler and more reliable.
    """
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs

    def execute(self) -> Dict[str, Any]:
        """
        Resolve chunk_ids to filenames.
        
        Strategy:
        1. Extract file_name from ChromaDB chunk metadata (fastest, preferred)
        2. If metadata missing, query Elasticsearch by chunk_id (now works with unified IDs!)
        
        Returns:
            Dict with status, references list, and metadata
            - status: "completed" or "failed"
            - references: List[Dict] items with keys: chunk_id, file_name, chunk_text
            - error: Error message if failed
        """
        try:
            # Extract inputs
            client_id = self.inputs['client_id']
            project_id = self.inputs['project_id']
            language = self.inputs.get('language', 'en')
            relevant_chunks = self.inputs['search_relevant_chunks']['relevant_chunks']
            
            logger.info(f"GetVectorReference: Processing {len(relevant_chunks)} chunks for {language}/{client_id}/{project_id}")
            
            if not relevant_chunks:
                logger.warning("No relevant chunks to process")
                return {
                    "status": "completed",
                    "references": [],
                    "total_mapped": 0,
                    "total_requested": 0,
                    "source": "no_chunks"
                }
            
            # Strategy 1: Extract file_name from ChromaDB metadata (preferred)
            references = []
            chunk_ids_without_metadata = []
            
            for i, chunk in enumerate(relevant_chunks):
                chunk_id = chunk.get('chunk_id') or chunk.get('metadata', {}).get('chunk_id')
                metadata = chunk.get('metadata', {})
                chunk_text = chunk.get('text', '')
                
                # Try to get file_name from metadata
                file_name = (
                    metadata.get('file_name') or 
                    metadata.get('filename') or 
                    metadata.get('object_name') or
                    metadata.get('source') or
                    metadata.get('file_path')
                )
                
                if i < 3:  # Log first 3 chunks for debugging
                    logger.info(f"Chunk {i}: chunk_id={chunk_id[:16]}..., file_name={file_name}")
                
                if file_name and chunk_id:
                    # Extract just the filename if it's a path
                    import os
                    file_name = os.path.basename(file_name) if ('/' in file_name or '\\' in file_name) else file_name
                    references.append({
                        "chunk_id": chunk_id,
                        "file_name": file_name,
                        "chunk_text": chunk_text,
                    })
                elif chunk_id:
                    chunk_ids_without_metadata.append(chunk_id)
                else:
                    logger.warning(f"Chunk {i} has no chunk_id")
            
            logger.info(f"Extracted {len(references)} references from metadata, {len(chunk_ids_without_metadata)} need ES lookup")
            
            # If all references found from metadata, return immediately
            if len(references) == len(relevant_chunks):
                logger.info(f"✅ All {len(references)} references extracted from ChromaDB metadata")
                return {
                    "status": "completed",
                    "references": references,
                    "total_mapped": len(references),
                    "total_requested": len(relevant_chunks),
                    "source": "chromadb_metadata"
                }
            
            # Strategy 2: Query Elasticsearch for missing chunk_ids (unified IDs make this work!)
            if chunk_ids_without_metadata:
                logger.info(f"Querying Elasticsearch for {len(chunk_ids_without_metadata)} chunk_ids")
                
                from libs.database_service.doc_db import ElasticsearchDocProvider
                import asyncio
                
                # Build index name with language
                index_name = f"chunk-embeddings-{language}-{client_id}-{project_id}"
                
                async def _fetch_from_elasticsearch():
                    try:
                        doc_provider = ElasticsearchDocProvider()
                        await doc_provider.initialize()
                        
                        # Query by chunk_id using terms query
                        query = {
                            "query": {
                                "terms": {
                                    "chunk_id.keyword": chunk_ids_without_metadata
                                }
                            }
                        }
                        
                        results = await doc_provider.search(
                            index=index_name,
                            query=query,
                            size=len(chunk_ids_without_metadata),
                            client_id=None  # Index name already scoped
                        )
                        
                        # Fallback: try without .keyword if no results
                        if not results:
                            logger.warning("No results with chunk_id.keyword, trying without .keyword")
                            query["query"]["terms"] = {"chunk_id": chunk_ids_without_metadata}
                            results = await doc_provider.search(
                                index=index_name,
                                query=query,
                                size=len(chunk_ids_without_metadata),
                                client_id=None
                            )
                        
                        logger.info(f"Elasticsearch returned {len(results)} documents from {index_name}")
                        return results
                        
                    except Exception as e:
                        logger.error(f"Elasticsearch query failed: {e}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        return []
                
                es_results = asyncio.run(_fetch_from_elasticsearch())
                
                # Add ES results to references
                for doc in es_results:
                    chunk_id = doc.get('chunk_id')
                    file_name = doc.get('file_name')
                    if chunk_id and file_name:
                        references.append({
                            "chunk_id": chunk_id,
                            "file_name": file_name,
                            "chunk_text": "",
                        })
                        logger.debug(f"Mapped chunk {chunk_id[:16]}... to {file_name}")
                
                logger.info(f"Added {len(es_results)} references from Elasticsearch")
            
            # Final result
            total_mapped = len(references)
            total_requested = len(relevant_chunks)
            
            if total_mapped == 0:
                error_msg = (
                    f"Could not map any chunk_ids to filenames. "
                    f"Requested {total_requested} chunks but found 0 references. "
                    f"This may indicate: "
                    f"1) Data not indexed in Elasticsearch, "
                    f"2) Index name mismatch, or "
                    f"3) Missing metadata in ChromaDB"
                )
                logger.error(error_msg)
                return {
                    "status": "failed",
                    "error": error_msg,
                    "references": [],
                    "total_mapped": 0,
                    "total_requested": total_requested
                }
            
            # Success!
            source = "chromadb_metadata" if len(chunk_ids_without_metadata) == 0 else "chromadb_and_elasticsearch"
            logger.info(f"✅ Successfully mapped {total_mapped}/{total_requested} chunk_ids to filenames")
            
            return {
                "status": "completed",
                "references": references,
                "total_mapped": total_mapped,
                "total_requested": total_requested,
                "source": source
            }
            
        except Exception as e:
            logger.error(f"GetVectorReference failed: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            return {
                "status": "failed",
                "error": str(e),
                "references": [],
                "total_mapped": 0
            }

class FetchChatHistory:
    """Fetch recent chat history for the current session"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs

    def execute(self) -> Dict[str, Any]:
        """Fetch recent chat history messages"""
        logger.info('########################## FetchChatHistory ##########################')
        logger.info(f'{self.inputs=}')

        client_id = self.inputs.get('client_id')
        project_id = self.inputs.get('project_id')
        session_id = self.inputs.get('session_id')
        user_id = self.inputs.get('user_id')
        limit = self.inputs.get('limit', 10)  # Default to 10 messages
        
        if not all([client_id, project_id, session_id]):
            logger.warning("Missing required inputs for chat history fetch")
            return {
                "chat_history": [],
                "history_metadata": {
                    "total_messages": 0,
                    "limit": limit,
                    "client_id": client_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "user_id": user_id
                }
            }

        try:
            from libs.database_service.sql_db.providers import PgSQLProvider
            
            # Initialize database provider
            db = PgSQLProvider()
            
            # Fetch recent messages
            messages = db.get_recent_messages(
                client_id=client_id,
                project_id=project_id,
                session_id=session_id,
                user_id=user_id,
                limit=limit
            )
            
            logger.info(f"Fetched {len(messages)} messages from chat history")
            
            # Convert datetime objects to ISO format strings for JSON serialization
            serialized_messages = []
            for message in messages:
                message_dict = dict(message)
                if 'created_at' in message_dict and hasattr(message_dict['created_at'], 'isoformat'):
                    message_dict['created_at'] = message_dict['created_at'].isoformat()
                serialized_messages.append(message_dict)
            
            # Log sample of messages for debugging
            if serialized_messages:
                sample_message = serialized_messages[0]
                logger.info(f"Sample message: {sample_message.get('role', 'unknown')} - {sample_message.get('content', '')[:50]}...")
            
            return {
                "chat_history": serialized_messages,
                "history_metadata": {
                    "total_messages": len(serialized_messages),
                    "limit": limit,
                    "client_id": client_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "user_id": user_id
                }
            }
            
        except Exception as e:
            logger.error(f"Error fetching chat history: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            return {
                "chat_history": [],
                "history_metadata": {
                    "total_messages": 0,
                    "limit": limit,
                    "client_id": client_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "user_id": user_id,
                    "error": str(e)
                }
            }


class CombineVectorResponseAndReferences:
    """Format LLM response and vector references into a compact payload.

    Inputs expected:
      - run_vector_rag: the raw LLM response (string or dict)
      - GetVectorReference: object with key 'references' = List[{chunk_id: file_name}]
      - search_relevant_chunks: object with key 'relevant_chunks' = List[chunk dicts]

    Output:
      {
        "llm_response": <clean string>,
        "references": "<json stringified list of {file_name, chunk_id, embedding?}>"
      }
    """

    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs

    def execute(self) -> Dict[str, Any]:
        import json

        # Extract and clean LLM response
        llm_raw = self.inputs.get("run_vector_rag")
        if isinstance(llm_raw, (dict, list)):
            llm_text = json.dumps(llm_raw, ensure_ascii=False)
        elif llm_raw is None:
            llm_text = ""
        else:
            llm_text = str(llm_raw)
        llm_text = llm_text.strip()

        # Build references by joining GetVectorReference mappings with retrieved chunks
        gvr = self.inputs.get("GetVectorReference", {}) or {}
        references_mappings: List[Dict[str, str]] = gvr.get("references", []) or []

        search_result = self.inputs.get("search_relevant_chunks", {}) or {}
        relevant_chunks: List[Dict[str, Any]] = search_result.get("relevant_chunks", []) or []

        # Index chunks by chunk_id for fast lookup; support multiple possible locations
        chunk_by_id: Dict[str, Dict[str, Any]] = {}
        for chunk in relevant_chunks:
            chunk_id = (
                chunk.get("chunk_id")
                or (chunk.get("metadata") or {}).get("chunk_id")
                or (chunk.get("embedding_metadata") or {}).get("chunk_id")
            )
            if chunk_id:
                chunk_by_id[chunk_id] = chunk

        formatted_references: List[Dict[str, Any]] = []
        for mapping in references_mappings:
            # Each mapping is like {chunk_id: file_name}
            if not isinstance(mapping, dict) or not mapping:
                continue
            # Extract the single pair
            [(cid, file_name)] = list(mapping.items())[:1]

            chunk = chunk_by_id.get(cid, {})
            embedding = (
                chunk.get("embedding")
                or (chunk.get("metadata") or {}).get("embedding")
                or (chunk.get("embedding_metadata") or {}).get("embedding")
            )

            formatted_references.append({
                "file_name": file_name,
                "chunk_id": cid,
                "embedding": embedding,
            })

        # Serialize references as JSON string per requirement
        references_json = json.dumps(formatted_references, ensure_ascii=False, default=str)

        return {
            "llm_output": llm_text,
            "references": references_json,
        }

class PassThrough:
    """Pass through the input unchanged - useful for pipeline debugging or data flow"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key

    def execute(self) -> Dict[str, Any]:
        """Return the input unchanged"""
        logger.info('########################## PassThrough ##########################')
        logger.info(f'Passing through inputs unchanged for pipeline_key: {self.pipeline_key}')
        return self.inputs


# Pipeline operations mapping - only include the classes that are actually used
pipeline_operations: Dict[str, Any] = {
    "GetFiles": GetFiles,
    "ParseDocuments": ParseDocuments,
    # New split pipeline steps
    "chunk_documents": ChunkDocuments,
    # Document preprocessing pipeline operations
    "UploadToObjectStorage": UploadToObjectStorage,
    "ParseDocumentToMarkdown": ParseDocumentToMarkdown,
    "ChunkDocument": ChunkDocument,
    "GenerateChunkEmbeddings": GenerateChunkEmbeddings,
    "StoreChunksInVectorDB": StoreChunksInVectorDB,
    "save_mapping_to_document_db": SaveMappingToDocumentDB,
    "extract_user_facts": ExtractUserFacts,
    "fetch_user_facts": FetchUserFacts,
    "save_user_message": SaveUserMessage,
    "save_vector_llm_message": SaveVectorLLMMessage,
    "search_relevant_chunks": SearchRelevantChunks,
    "GetVectorReference": GetVectorReference,
    "fetch_chat_history": FetchChatHistory,
    # Utility operations
    "combine_vector_response_and_references": CombineVectorResponseAndReferences,
    "PassThrough": PassThrough,
}

def log_processing_details(pipeline_key, inputs=None, results=None, input_item=None, stage="start"):
    """Log processing details for debugging pipeline execution."""
    steps = {
        "start": f"Executing pipeline step for pipeline_key: {pipeline_key}",
        "operation_found": f"Found operation for pipeline_key: {pipeline_key}",
        "processing_input": f"Processing input_item: {input_item}",
        "end": f"Completed processing for {pipeline_key}. Results: {results}"
    }
    logger.info(steps[stage])





async def process_operation_async(operation, inputs, pipeline_key, project_name, prompt_config):
    """Process operation with proper async handling."""
    try:
        if hasattr(operation, 'execute'):
            # Check if execute method is async
            import inspect
            if inspect.iscoroutinefunction(operation.execute):
                return await operation.execute()
            else:
                return operation.execute()
        else:
            return operation
    except Exception as e:
        logger.error(f"Error in process_operation_async for {pipeline_key}: {e}")
        raise

def process_operation(operation, inputs, pipeline_key, project_name, prompt_config):
    """Process operation with proper handling."""
    try:
        # Global skip gate for operation-based steps
        try:
            flat_inputs = flatten_dict(inputs) if isinstance(inputs, dict) else {}
            if any('SkiPeD!!' in str(v) for v in flat_inputs.values()):
                logger.info(f"Operation step '{pipeline_key}' skipped due to SkiPeD!! in inputs")
                return {"output": "SkiPeD!!"}
        except Exception:
            pass

        if hasattr(operation, 'execute'):
            return operation.execute()
        else:
            return operation
    except Exception as e:
        logger.error(f"Error in process_operation for {pipeline_key}: {e}")
        raise





def execute_pipeline_step(inputs: Dict[str, Any], project_name: str, prompt_config: Dict[str, Any], pipeline_key: str, json_object: bool = False, domain_id: str = None, save_to_db: str = None) -> Any:
    """Execute a pipeline step with proper error handling and logging."""
    log_processing_details(pipeline_key, stage="start")
    
    try:
        if pipeline_key in pipeline_operations:
            operation_cls = pipeline_operations[pipeline_key]
            log_processing_details(pipeline_key, stage="operation_found")
            
            # Create operation instance
            operation = operation_cls(inputs, project_name, prompt_config, pipeline_key)
            
            # Process operation with proper async handling
            operation_results = process_operation(operation, inputs, pipeline_key, project_name, prompt_config)
            
            log_processing_details(pipeline_key, results=operation_results, stage="end")
            
            # Store results if save_to_db is specified
            if save_to_db:
                _store_step_results(pipeline_key, operation_results, project_name, save_to_db, pipeline_key)
            
            return operation_results
        else:
            # Clean else fallback: treat pipeline_key as prompt_key
            # Fetch from PromptStore (Langfuse) and execute via LLMGateway
            logger.info(f"Pipeline key '{pipeline_key}' not in operations, treating as prompt-based step")
            
            processed_inputs = inputs
            
            llm_gateway = LLMGateway()
            
            operation_results = llm_gateway.send_request_sync(
                processed_inputs, project_name, prompt_config, pipeline_key, json_object=json_object, domain_id=domain_id
            )
            log_processing_details(pipeline_key, results=operation_results, stage="end")
            
            # Store results if save_to_db is specified
            if save_to_db:
                _store_step_results(pipeline_key, operation_results, project_name, save_to_db, pipeline_key)
            
            return operation_results
            
    except Exception as e:
        error_msg = f"Pipeline step {pipeline_key} failed: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Error type: {type(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise Exception(error_msg) from e


def _store_step_results(step_name: str, results: Any, project_name: str, storage_type: str, pipeline_key: str):
    """Helper function to store pipeline step results"""
    try:
        store_results = get_store_results()
        storage_key = store_results.store_step_results_sync(
            step_name=step_name,
            data=results,
            project_name=project_name,
            storage_type=storage_type,
            pipeline_key=pipeline_key,
            additional_metadata={
                "step_type": "pipeline_operation" if step_name in pipeline_operations else "prompt_based"
            }
        )
        
        if storage_key:
            logger.info(f"Successfully stored {step_name} results with key: {storage_key}")
        else:
            logger.warning(f"Failed to store {step_name} results")
            
    except Exception as e:
        logger.error(f"Error storing {step_name} results: {e}")
        # Don't raise exception to avoid breaking the pipeline
