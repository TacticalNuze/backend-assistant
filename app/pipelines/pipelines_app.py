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
from libs.ragas_service.service import evaluate_with_langfuse

logger = logging.getLogger(__name__)

def get_input_hash(inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str) -> Tuple[str, str]:
    formatted_input_data = json.dumps(inputs, sort_keys=True)
    import hashlib
    input_hash = hashlib.sha256(formatted_input_data.encode()).hexdigest()
    return formatted_input_data, input_hash


def normalize_query(query: str) -> str:
    """Normalize a query string for consistent matching.
    
    This function:
    - Strips leading/trailing whitespace
    - Replaces newlines and carriage returns with spaces
    - Collapses multiple spaces into single space
    
    This ensures queries can be matched consistently even if they have
    different whitespace formatting.
    """
    if not isinstance(query, str):
        return str(query) if query else ""
    normalized = query.strip().replace('\n', ' ').replace('\r', ' ')
    normalized = ' '.join(normalized.split())
    return normalized


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


class EvaluateRAGWithRagas:
    """
    Evaluate RAG response using RAGAS metrics with Langfuse tracing.
    
    Inputs expected:
      - input_text: The user query/question (required)
      - run_vector_rag: The LLM response (required)
      - search_relevant_chunks: Object with 'relevant_chunks' list (required)
      - reference: Optional ground truth/reference answer for evaluation
    
    Output:
      {
        "ragas_scores": {
          "ContextPrecision": float,
          "ContextRecall": float,
          "ContextRelevance": float
        },
        "evaluation_metadata": {
          "num_contexts": int,
          "has_reference": bool,
          "trace_id": str (if Langfuse tracing enabled)
        }
      }
    """
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Execute RAGAS evaluation on RAG response"""
        logger.info('########################## EvaluateRAGWithRagas ##########################')
        logger.info(f'{self.pipeline_key=}')
        
        try:
            # Extract required inputs
            user_input = self.inputs.get("input_text")
            llm_response = self.inputs.get("run_vector_rag")
            
            # Extract retrieved contexts from search_relevant_chunks
            search_result = self.inputs.get("search_relevant_chunks", {}) or {}
            relevant_chunks = search_result.get("relevant_chunks", []) or []
            
            # Extract optional reference (ground truth)
            reference = self.inputs.get("reference") or self.inputs.get("ground_truth")
            
            # Validate required inputs
            if not user_input:
                logger.warning("Missing required input: input_text")
                return {
                    "ragas_scores": {},
                    "evaluation_metadata": {
                        "error": "Missing input_text",
                        "num_contexts": 0,
                        "has_reference": False
                    }
                }
            
            if not llm_response:
                logger.warning("Missing required input: run_vector_rag")
                return {
                    "ragas_scores": {},
                    "evaluation_metadata": {
                        "error": "Missing run_vector_rag",
                        "num_contexts": 0,
                        "has_reference": False
                    }
                }
            
            # Convert LLM response to string if needed
            if isinstance(llm_response, (dict, list)):
                response_text = json.dumps(llm_response, ensure_ascii=False)
            else:
                response_text = str(llm_response).strip()
            
            # Extract text from relevant chunks
            retrieved_contexts = []
            for chunk in relevant_chunks:
                # Handle different chunk formats
                chunk_text = (
                    chunk.get("text") or
                    chunk.get("content") or
                    (chunk.get("metadata") or {}).get("text") or
                    (chunk.get("metadata") or {}).get("content") or
                    str(chunk)
                )
                if chunk_text:
                    retrieved_contexts.append(str(chunk_text))
            
            logger.info(f"Evaluating RAG response with {len(retrieved_contexts)} contexts")
            if reference:
                logger.info("Reference/ground truth provided for evaluation")
            
            
            
            rag_output = {
                "user_input": user_input,
                "retrieved_contexts": retrieved_contexts,
                "response": response_text
            }
            if reference:
                rag_output["reference"] = reference
            
            # Run RAGAS evaluation with Langfuse tracing asynchronously
            evaluation_result = asyncio.run(
                evaluate_with_langfuse(
                    row=rag_output,
                    trace_name="ragas_evaluation"
                )
            )
            
            # Extract scores and trace_id from result
            scores = evaluation_result.get("scores", {})
            trace_id = evaluation_result.get("trace_id")
            
            logger.info(f"RAGAS evaluation completed. Scores: {scores}")
            if trace_id:
                logger.info(f"Langfuse trace ID: {trace_id}")
            
            return {
                "ragas_scores": scores,
                "evaluation_metadata": {
                    "num_contexts": len(retrieved_contexts),
                    "has_reference": reference is not None,
                    "user_input": user_input,
                    "response_length": len(response_text),
                    "trace_id": trace_id
                }
            }
            
        except Exception as e:
            logger.error(f"Error in RAGAS evaluation: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            return {
                "ragas_scores": {},
                "evaluation_metadata": {
                    "error": str(e),
                    "num_contexts": 0,
                    "has_reference": False
                }
            }


class RetrieveRandomChunks:
    """Retrieve random chunks from ChromaDB for evaluation dataset generation"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Retrieve random chunks from ChromaDB"""
        import asyncio
        from libs.database_service.service import DatabaseService
        
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        language = self.inputs.get("language", "en")
        # Use num_queries if provided, otherwise num_chunks, default to 100
        num_queries = self.inputs.get("num_queries")
        num_chunks = self.inputs.get("num_chunks")
        
        # Calculate num_chunks: get more chunks than queries to ensure we have enough
        if num_queries:
            num_chunks = num_queries * 2
        elif not num_chunks:
            num_chunks = 100
        
        if not all([client_id, project_id]):
            logger.error("Missing required inputs: client_id and project_id")
            return {"chunks": [], "error": "Missing required inputs"}
        
        return asyncio.run(self._retrieve_chunks_async(client_id, project_id, language, num_chunks))
    
    async def _retrieve_chunks_async(self, client_id: str, project_id: str, language: str, num_chunks: int) -> Dict[str, Any]:
        """Async method to retrieve random chunks"""
        from libs.database_service.service import DatabaseService
        import random
        
        db_service = DatabaseService()
        await db_service.initialize()
        
        chroma_provider = db_service.vector_manager.provider
        collection_name = f"chunks_{language}_{client_id}_{project_id}"
        chroma_provider.base_collection_name = collection_name
        
        def _get_chunks_sync():
                collection_name = chroma_provider._get_collection_name(client_id)
                collection = chroma_provider.client.get_collection(collection_name)
                total_count = collection.count()
                if total_count == 0:
                    return []
                
                limit = min(num_chunks * 2, total_count)
                results = collection.get(
                    limit=limit,
                    where={"project_id": project_id} if project_id else None
                )
                
                chunks = []
                if results.get("documents") and results.get("ids"):
                    for i, doc_text in enumerate(results["documents"]):
                        chunk_id = results["ids"][i] if i < len(results["ids"]) else None
                        metadata = results["metadatas"][i] if results.get("metadatas") and i < len(results["metadatas"]) else {}
                        chunks.append({
                            "text": doc_text,
                            "chunk_id": chunk_id,
                            "metadata": metadata
                        })
                
                if len(chunks) > num_chunks:
                    chunks = random.sample(chunks, num_chunks)
                
                return chunks
        
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, _get_chunks_sync)
        
        await db_service.close()
        
        logger.info(f"Retrieved {len(chunks)} random chunks from ChromaDB")
        return {"chunks": chunks, "num_chunks": len(chunks)}


class GenerateQueriesFromChunks:
    """Generate queries from chunks using LLM (prompt-based step)"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Generate queries from chunks using prompt-based step"""
        import asyncio
        
        # Get chunks from previous step
        retrieve_result = self.inputs.get("retrieve_random_chunks", {})
        if isinstance(retrieve_result, dict):
            chunks = retrieve_result.get("chunks", [])
        else:
            chunks = []
        
        num_queries = self.inputs.get("num_queries", len(chunks))
        query_generation_prompt_key = self.inputs.get("query_generation_prompt_key", "generate-query-from-chunk")
        
        if not chunks:
            logger.warning("No chunks provided for query generation")
            return {"queries": [], "num_queries": 0}
        
        queries = asyncio.run(self._generate_queries_async(
            chunks=chunks[:num_queries],
            prompt_key=query_generation_prompt_key
        ))
        
        # Return in consistent format - list of queries
        # This matches what SearchContextsForQueries and ProcessEvalQueriesBatch expect
        if isinstance(queries, list):
            return queries
        else:
            # Fallback if _generate_queries_async returns something else
            return queries.get("queries", []) if isinstance(queries, dict) else []
    
    async def _generate_queries_async(self, chunks: List[Dict[str, Any]], prompt_key: str) -> Dict[str, Any]:
        """Async method to generate queries from chunks in batch"""
        llm_gateway = LLMGateway()
        
        # Filter and prepare chunks
        valid_chunks = []
        for chunk in chunks:
            chunk_text = chunk.get("text", "")
            if chunk_text and len(chunk_text.strip()) >= 50:
                valid_chunks.append(chunk_text[:2000])  # Limit chunk size
        
        if not valid_chunks:
            logger.warning("No valid chunks for query generation")
            return []
        
        try:
            # Generate queries in batch - combine all chunks into a single prompt
            chunks_text = "\n\n---\n\n".join([f"Chunk {i+1}:\n{chunk}" for i, chunk in enumerate(valid_chunks)])
            
            prompt_variables = {
                "chunk_text": chunks_text,
                "num_chunks": len(valid_chunks)
            }
            
            logger.info(f"Generating queries in batch for {len(valid_chunks)} chunks")
            query_response = await llm_gateway.generate(
                prompt_key=prompt_key,
                variables=prompt_variables,
                temperature=0.7,
                max_tokens=4000
            )
            
            if query_response:
                # Parse response to extract all questions
                queries = []
                response_text = str(query_response).strip()
                
                # Split by newlines and process each line
                for line in response_text.split('\n'):
                    line = line.strip()
                    # Remove numbering if present
                    if line and (line[0].isdigit() or line.startswith('-')):
                        line = line.split('.', 1)[-1].strip()
                    if line and len(line) > 10:  # Valid question
                        # Normalize query for consistent matching
                        line_normalized = normalize_query(line)
                        queries.append(line_normalized)
                
                # If no questions found but response is long, treat entire response as one query
                if not queries and len(response_text) > 10:
                    queries.append(response_text)
                
                logger.info(f"Generated {len(queries)} queries in batch from {len(valid_chunks)} chunks")
                return queries
            else:
                logger.warning("No response from LLM for batch query generation")
                return []
                
        except Exception as e:
            logger.error(f"Error generating queries in batch: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []


class SearchContextsForQueries:
    """Search relevant chunks for multiple queries"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Search contexts for all queries"""
        # Get queries from previous step
        generate_queries_input = self.inputs.get("generate_queries", [])
        
        # Handle different input formats
        if isinstance(generate_queries_input, str):
            queries = [generate_queries_input]
        elif isinstance(generate_queries_input, list):
            queries = [q for q in generate_queries_input if isinstance(q, str)]
        else:
            queries = []
        
        if not queries:
            logger.warning("No queries provided for context search")
            return {"search_results": {}}
        
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        language = self.inputs.get("language", "en")
        chunks_per_query = self.inputs.get("chunks_per_query", 5)
        embedding_model = self.inputs.get("embedding_model", "text-embedding-3-large")
        embedding_provider = self.inputs.get("embedding_provider", "azure_openai")
        
        logger.info(f"Searching contexts for {len(queries)} queries")
        
        # Search contexts for each query
        # Normalize queries and use normalized version as primary key for consistent matching
        search_results = {}
        query_to_normalized = {}  # Map original query to normalized version
        
        for query in queries:
            try:
                # Normalize query for consistent matching
                query_normalized = normalize_query(query)
                query_to_normalized[query] = query_normalized
                
                logger.info(f"Searching contexts for query {len(search_results) + 1}/{len(queries)}: '{query[:80]}...' (normalized: '{query_normalized[:80]}...')")
                search_inputs = {
                    "input_text": query,  # Use original query for search
                    "client_id": client_id,
                    "project_id": project_id,
                    "language": language,
                    "top_k": chunks_per_query,
                    "embedding_model": embedding_model,
                    "embedding_provider": embedding_provider
                }
                
                search_operation = SearchRelevantChunks(
                    inputs=search_inputs,
                    project_name=self.project_name,
                    prompt_config_src=self.prompt_config_src,
                    pipeline_key="search_relevant_chunks"
                )
                
                search_result = search_operation.execute()
                # Store with normalized key as primary, but also store with original for backward compatibility
                search_results[query_normalized] = search_result
                if query != query_normalized:
                    search_results[query] = search_result  # Also store with original for fallback
                
            except Exception as e:
                logger.error(f"Error searching contexts for query '{query[:50]}...': {e}")
                search_results[query] = {
                    "relevant_chunks": [],
                    "search_metadata": {"error": str(e)}
                }
        
        logger.info(f"Completed context search for {len(queries)} queries (stored {len(search_results)} search result entries)")
        logger.debug(f"Sample normalized query keys (first 3): {list(search_results.keys())[:3]}")
        return {"search_results": search_results}


class ProcessEvalQueriesBatch:
    """Process queries in batch: search contexts, generate ground truth, and optionally RAG response for multiple queries"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Process queries in batch for evaluation dataset"""
        import asyncio
        
        # Get queries from previous step
        generate_queries_input = self.inputs.get("generate_queries", [])
        
        # Handle different input formats
        if isinstance(generate_queries_input, str):
            queries = [generate_queries_input]
        elif isinstance(generate_queries_input, list):
            queries = [q for q in generate_queries_input if isinstance(q, str)]
        else:
            queries = []
        
        if not queries:
            logger.warning("No queries provided for batch processing")
            return {"processed_queries": []}
        
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        language = self.inputs.get("language", "en")
        chunks_per_query = self.inputs.get("chunks_per_query", 5)
        embedding_model = self.inputs.get("embedding_model", "text-embedding-3-large")
        embedding_provider = self.inputs.get("embedding_provider", "azure_openai")
        ground_truth_prompt_key = self.inputs.get("ground_truth_prompt_key", "generate-ground-truth")
        generate_rag_response = self.inputs.get("generate_rag_response", True)
        rag_prompt_key = self.inputs.get("rag_prompt_key", "run-vector-rag")
        batch_size = self.inputs.get("batch_size", 10)  # Process queries in batches
        
        logger.info(f"Processing {len(queries)} queries in batches of {batch_size}")
        
        return asyncio.run(self._process_queries_batch_async(
            queries=queries,
            client_id=client_id,
            project_id=project_id,
            language=language,
            chunks_per_query=chunks_per_query,
            embedding_model=embedding_model,
            embedding_provider=embedding_provider,
            ground_truth_prompt_key=ground_truth_prompt_key,
            generate_rag_response=generate_rag_response,
            rag_prompt_key=rag_prompt_key,
            batch_size=batch_size
        ))
    
    async def _process_queries_batch_async(
        self,
        queries: List[str],
        client_id: str,
        project_id: str,
        language: str,
        chunks_per_query: int,
        embedding_model: str,
        embedding_provider: str,
        ground_truth_prompt_key: str,
        generate_rag_response: bool,
        rag_prompt_key: str,
        batch_size: int
    ) -> Dict[str, Any]:
        """Process queries in batches"""
        processed_results = []
        
        # Get search results from inputs (from previous workflow step) - do this once before batches
        search_contexts_input = self.inputs.get("search_contexts", {})
        # Extract the search_results dict from the step output
        if isinstance(search_contexts_input, dict) and "search_results" in search_contexts_input:
            search_results_input = search_contexts_input["search_results"]
        else:
            search_results_input = search_contexts_input
        
        logger.info(f"Search results format: {type(search_results_input).__name__}, keys/items: {len(search_results_input) if isinstance(search_results_input, (dict, list)) else 'N/A'}")
        
        # Process queries in batches
        for batch_start in range(0, len(queries), batch_size):
            batch_end = min(batch_start + batch_size, len(queries))
            batch_queries = queries[batch_start:batch_end]
            
            logger.info(f"Processing batch {batch_start//batch_size + 1}: queries {batch_start+1}-{batch_end} of {len(queries)}")
            
            # Process each query in the batch
            batch_results = []
            for batch_idx, query in enumerate(batch_queries):
                global_idx = batch_start + batch_idx  # Global index across all queries
                
                # Normalize query the same way as SearchContextsForQueries does
                query_normalized = normalize_query(query)
                
                logger.info(f"[Query {global_idx + 1}/{len(queries)}] Processing: '{query[:80]}...' (normalized: '{query_normalized[:80]}...')")
                logger.info(f"[Query {global_idx + 1}] Batch index: {batch_idx}, Global index: {global_idx}, Total queries: {len(queries)}")
                
                try:
                    # Get search result for this query
                    search_result = None
                    if isinstance(search_results_input, dict):
                        # Try normalized version first (primary key from SearchContextsForQueries)
                        search_result = search_results_input.get(query_normalized)
                        if search_result:
                            logger.info(f"[Query {global_idx + 1}] ✓ Found match using normalized query key")
                            logger.debug(f"[Query {global_idx + 1}] Search result type: {type(search_result)}, has relevant_chunks: {isinstance(search_result, dict) and 'relevant_chunks' in search_result}")
                        
                        # Fallback to original query if normalized didn't match
                        if search_result is None:
                            search_result = search_results_input.get(query)
                            if search_result:
                                logger.info(f"[Query {global_idx + 1}] ✓ Found match using original query key")
                                logger.debug(f"[Query {global_idx + 1}] Search result type: {type(search_result)}, has relevant_chunks: {isinstance(search_result, dict) and 'relevant_chunks' in search_result}")
                        
                        # Final fallback: iterate and compare normalized versions
                        if search_result is None:
                            logger.info(f"[Query {global_idx + 1}] Attempting iterative matching...")
                            for key in search_results_input.keys():
                                key_normalized = normalize_query(key)
                                if key_normalized == query_normalized:
                                    search_result = search_results_input[key]
                                    logger.info(f"[Query {global_idx + 1}] ✓ Found match after normalizing and comparing")
                                    logger.debug(f"[Query {global_idx + 1}] Matched key: '{key[:80]}...' (normalized: '{key_normalized[:80]}...')")
                                    break
                        
                        if search_result is None:
                            logger.warning(f"[Query {global_idx + 1}] ✗ Query not found in search_results dict")
                            logger.warning(f"[Query {global_idx + 1}] Query (original): '{query}'")
                            logger.warning(f"[Query {global_idx + 1}] Query (normalized): '{query_normalized}'")
                            logger.warning(f"[Query {global_idx + 1}] Query length: {len(query)}, Normalized length: {len(query_normalized)}")
                            if search_results_input:
                                sample_keys = list(search_results_input.keys())[:5]
                                logger.warning(f"[Query {global_idx + 1}] Sample search_result keys (first 5): {[k[:80] + '...' if len(k) > 80 else k for k in sample_keys]}")
                                logger.warning(f"[Query {global_idx + 1}] Total search_results: {len(search_results_input)}")
                            else:
                                logger.error(f"[Query {global_idx + 1}] ✗ search_results_input dict is empty!")
                    elif isinstance(search_results_input, list):
                        # Use global index for list format
                        if global_idx < len(search_results_input):
                            search_result = search_results_input[global_idx]
                            logger.info(f"[Query {global_idx + 1}] ✓ Found match using list index {global_idx}")
                        else:
                            logger.warning(f"[Query {global_idx + 1}] ✗ Global index {global_idx} out of range for search_results list (length: {len(search_results_input)})")
                    else:
                        logger.error(f"[Query {global_idx + 1}] ✗ Unexpected search_results_input type: {type(search_results_input)}")
                    
                    if search_result is None:
                        logger.error(f"[Query {global_idx + 1}] ✗ No search result found for query: '{query[:50]}...'")
                        error_result = {
                            "query": query,
                            "error": "No search result found",
                            "contexts": [],
                            "ground_truth": "",
                            "response": ""
                        }
                        batch_results.append(error_result)
                        logger.info(f"[Query {global_idx + 1}] Added error result to batch (batch_results length: {len(batch_results)})")
                        continue
                    
                    # Verify search_result structure
                    if isinstance(search_result, dict):
                        relevant_chunks = search_result.get("relevant_chunks", [])
                        logger.info(f"[Query {global_idx + 1}] Search result has {len(relevant_chunks)} relevant chunks")
                        if not relevant_chunks:
                            logger.warning(f"[Query {global_idx + 1}] ⚠ Search result has no relevant_chunks!")
                    else:
                        logger.warning(f"[Query {global_idx + 1}] ⚠ Search result is not a dict: {type(search_result)}")
                    
                    # Process single query with proper error handling
                    try:
                        logger.info(f"[Query {global_idx + 1}] Calling _process_single_query...")
                        result = await self._process_single_query(
                            query=query,
                            client_id=client_id,
                            project_id=project_id,
                            language=language,
                            chunks_per_query=chunks_per_query,
                            embedding_model=embedding_model,
                            embedding_provider=embedding_provider,
                            ground_truth_prompt_key=ground_truth_prompt_key,
                            generate_rag_response=generate_rag_response,
                            rag_prompt_key=rag_prompt_key,
                            search_results=search_result
                        )
                        
                        # Ensure result has all required fields
                        if not isinstance(result, dict):
                            logger.error(f"[Query {global_idx + 1}] ✗ Result is not a dict: {type(result)}")
                            result = {"query": query, "contexts": [], "ground_truth": "", "response": "", "error": "Invalid result format"}
                        
                        # Log detailed result summary
                        has_ground_truth = bool(result.get("ground_truth"))
                        has_response = bool(result.get("response"))
                        has_error = bool(result.get("error"))
                        has_contexts = bool(result.get("contexts"))
                        contexts_count = len(result.get("contexts", []))
                        ground_truth_len = len(result.get("ground_truth", ""))
                        response_len = len(result.get("response", ""))
                        
                        logger.info(f"[Query {global_idx + 1}] ========== RESULT SUMMARY ==========")
                        logger.info(f"[Query {global_idx + 1}] Query: '{query[:60]}...'")
                        logger.info(f"[Query {global_idx + 1}] Contexts: {contexts_count} chunks {'✓' if has_contexts else '✗'}")
                        logger.info(f"[Query {global_idx + 1}] Ground truth: {ground_truth_len} chars {'✓' if has_ground_truth else '✗'}")
                        logger.info(f"[Query {global_idx + 1}] RAG response: {response_len} chars {'✓' if has_response else '✗'}")
                        logger.info(f"[Query {global_idx + 1}] Error: {'✗' if has_error else '✓'}")
                        logger.info(f"[Query {global_idx + 1}] =====================================")
                        
                        # Ensure query is preserved in result
                        result["query"] = query
                        batch_results.append(result)
                        logger.info(f"[Query {global_idx + 1}] ✓ Added result to batch (batch_results length: {len(batch_results)})")
                    except Exception as inner_e:
                        logger.error(f"[Query {global_idx + 1}] ✗ Error in _process_single_query: {inner_e}")
                        import traceback
                        logger.error(f"[Query {global_idx + 1}] Traceback: {traceback.format_exc()}")
                        error_result = {
                            "query": query,
                            "error": f"Processing error: {str(inner_e)}",
                            "contexts": [],
                            "ground_truth": "",
                            "response": ""
                        }
                        batch_results.append(error_result)
                        logger.info(f"[Query {global_idx + 1}] Added error result to batch (batch_results length: {len(batch_results)})")
                except Exception as e:
                    logger.error(f"[Query {global_idx + 1}] ✗ Error processing query: {e}")
                    import traceback
                    logger.error(f"[Query {global_idx + 1}] Traceback: {traceback.format_exc()}")
                    error_result = {
                        "query": query,
                        "error": str(e),
                        "contexts": [],
                        "ground_truth": "",
                        "response": ""
                    }
                    batch_results.append(error_result)
                    logger.info(f"[Query {global_idx + 1}] Added error result to batch (batch_results length: {len(batch_results)})")
            
            # Log batch completion
            batch_ground_truths = sum(1 for r in batch_results if r.get("ground_truth"))
            batch_responses = sum(1 for r in batch_results if r.get("response"))
            logger.info(f"Batch {batch_start//batch_size + 1} completed: {len(batch_results)} queries, {batch_ground_truths} with ground truth, {batch_responses} with RAG response")
            
            processed_results.extend(batch_results)
        
        # Summary statistics
        total_queries = len(processed_results)
        queries_with_ground_truth = sum(1 for r in processed_results if r.get("ground_truth"))
        queries_with_response = sum(1 for r in processed_results if r.get("response"))
        queries_with_errors = sum(1 for r in processed_results if r.get("error"))
        queries_with_contexts = sum(1 for r in processed_results if r.get("contexts"))
        
        logger.info("=" * 80)
        logger.info(f"BATCH PROCESSING SUMMARY:")
        logger.info(f"  Total queries processed: {total_queries}")
        logger.info(f"  Queries with contexts: {queries_with_contexts}/{total_queries}")
        logger.info(f"  Queries with ground truth: {queries_with_ground_truth}/{total_queries}")
        logger.info(f"  Queries with RAG response: {queries_with_response}/{total_queries}")
        logger.info(f"  Queries with errors: {queries_with_errors}/{total_queries}")
        logger.info(f"  generate_rag_response flag: {generate_rag_response}")
        
        # Log sample of results for debugging
        if processed_results:
            sample_result = processed_results[0]
            logger.info(f"  Sample result keys: {list(sample_result.keys())}")
            logger.info(f"  Sample result has ground_truth: {bool(sample_result.get('ground_truth'))}")
            logger.info(f"  Sample result has response: {bool(sample_result.get('response'))}")
            logger.info(f"  Sample result has contexts: {bool(sample_result.get('contexts'))}")
        
        logger.info("=" * 80)
        
        return {"processed_queries": processed_results}
    
    async def _process_single_query(
        self,
        query: str,
        client_id: str,
        project_id: str,
        language: str,
        chunks_per_query: int,
        embedding_model: str,
        embedding_provider: str,
        ground_truth_prompt_key: str,
        generate_rag_response: bool,
        rag_prompt_key: str,
        search_results: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Process a single query"""
        # Step 1: Get search results from input (from previous step) or use provided results
        if search_results is None:
            # Try to get from inputs (from previous workflow step)
            search_results_input = self.inputs.get("search_contexts", {})
            # search_results_input should be a dict mapping queries to their search results
            # or a list of search results in the same order as queries
            if isinstance(search_results_input, dict):
                search_result = search_results_input.get(query, {})
            elif isinstance(search_results_input, list):
                # If it's a list, we need to find the matching query
                # This is less ideal, but handle it
                search_result = {}
                for sr in search_results_input:
                    if isinstance(sr, dict) and sr.get("query") == query:
                        search_result = sr.get("search_result", {})
                        break
            else:
                search_result = {}
        else:
            search_result = search_results
        
        # Extract relevant chunks from search result
        # search_result should be a dict with "relevant_chunks" key (from SearchRelevantChunks.execute())
        if isinstance(search_result, dict):
            relevant_chunks = search_result.get("relevant_chunks", [])
        else:
            relevant_chunks = []
        
        if not relevant_chunks:
            logger.warning(f"No contexts retrieved for query: {query[:50]}...")
            return {
                "query": query,
                "error": "No contexts retrieved",
                "contexts": [],
                "ground_truth": "",
                "response": ""
            }
        
        # Step 2: Extract context texts from relevant chunks
        # Each chunk should have a "text" field (from SearchRelevantChunks)
        context_texts = []
        for chunk in relevant_chunks:
            chunk_text = chunk.get("text", "")
            if chunk_text:
                context_texts.append(chunk_text)
        
        # Combine contexts (limit to 4000 chars to avoid token limits)
        contexts_combined = "\n\n".join(context_texts)
        if len(contexts_combined) > 4000:
            # Truncate if too long
            contexts_combined = contexts_combined[:4000] + "..."
        
        logger.info(f"Extracted {len(context_texts)} contexts for query '{query[:50]}...' (total length: {len(contexts_combined)} chars)")
        
        llm_gateway = LLMGateway()
        ground_truth = ""
        
        # Step 2: Generate ground truth (always attempt this)
        logger.info(f"[Ground Truth] ========== STARTING GROUND TRUTH GENERATION ==========")
        logger.info(f"[Ground Truth] Query: '{query[:80]}...'")
        logger.info(f"[Ground Truth] Contexts: {len(context_texts)} chunks, {len(contexts_combined)} total chars")
        logger.info(f"[Ground Truth] Prompt key: {ground_truth_prompt_key}")
        logger.info(f"[Ground Truth] Variables: query length={len(query)}, contexts length={len(contexts_combined)}")
        
        try:
            logger.info(f"[Ground Truth] Calling llm_gateway.generate()...")
            ground_truth_response = await llm_gateway.generate(
                prompt_key=ground_truth_prompt_key,
                variables={"query": query, "contexts": contexts_combined},
                temperature=0.3,
                max_tokens=500
            )
            
            logger.info(f"[Ground Truth] LLM call completed. Response type: {type(ground_truth_response)}")
            logger.debug(f"[Ground Truth] Raw response (first 200 chars): {str(ground_truth_response)[:200] if ground_truth_response else 'None'}...")
            
            ground_truth = str(ground_truth_response).strip() if ground_truth_response else ""
            if ground_truth:
                logger.info(f"✓ [Ground Truth] Successfully generated: {len(ground_truth)} chars")
                logger.info(f"[Ground Truth] First 150 chars: {ground_truth[:150]}...")
            else:
                logger.warning(f"⚠ [Ground Truth] Response was empty or None")
                logger.warning(f"[Ground Truth] Raw response type: {type(ground_truth_response)}")
                logger.warning(f"[Ground Truth] Raw response value: {repr(ground_truth_response)[:200]}")
        except Exception as e:
            logger.error(f"✗ [Ground Truth] Exception during generation: {e}")
            import traceback
            logger.error(f"[Ground Truth] Traceback: {traceback.format_exc()}")
            ground_truth = ""  # Ensure it's empty on error
        
        logger.info(f"[Ground Truth] Final ground_truth length: {len(ground_truth)} chars")
        logger.info(f"[Ground Truth] ========== GROUND TRUTH GENERATION COMPLETE ==========")
        
        # Step 3: Optionally generate RAG response
        response = ""
        logger.info(f"[RAG Response] ========== STARTING RAG RESPONSE GENERATION ==========")
        logger.info(f"[RAG Response] generate_rag_response flag: {generate_rag_response}")
        logger.info(f"[RAG Response] Query: '{query[:80]}...'")
        logger.info(f"[RAG Response] Contexts: {len(context_texts)} chunks, {len(contexts_combined)} total chars")
        
        if generate_rag_response:
            try:
                logger.info(f"[RAG Response] Prompt key: {rag_prompt_key}")
                logger.info(f"[RAG Response] Variables: input_text length={len(query)}, contexts length={len(contexts_combined)}")
                logger.info(f"[RAG Response] Calling llm_gateway.generate()...")
                
                rag_response = await llm_gateway.generate(
                    prompt_key=rag_prompt_key,
                    variables={"input_text": query, "contexts": contexts_combined},
                    temperature=0.7,
                    max_tokens=500
                )
                
                logger.info(f"[RAG Response] LLM call completed. Response type: {type(rag_response)}")
                logger.debug(f"[RAG Response] Raw response (first 200 chars): {str(rag_response)[:200] if rag_response else 'None'}...")
                
                response = str(rag_response).strip() if rag_response else ""
                if response:
                    logger.info(f"✓ [RAG Response] Successfully generated: {len(response)} chars")
                    logger.info(f"[RAG Response] First 150 chars: {response[:150]}...")
                else:
                    logger.warning(f"⚠ [RAG Response] Response was empty or None")
                    logger.warning(f"[RAG Response] Raw response type: {type(rag_response)}")
                    logger.warning(f"[RAG Response] Raw response value: {repr(rag_response)[:200]}")
            except Exception as e:
                logger.error(f"✗ [RAG Response] Exception during generation: {e}")
                import traceback
                logger.error(f"[RAG Response] Traceback: {traceback.format_exc()}")
                response = ""  # Ensure it's empty on error
        else:
            logger.info(f"[RAG Response] Skipping RAG response generation (generate_rag_response=False)")
        
        logger.info(f"[RAG Response] Final response length: {len(response)} chars")
        logger.info(f"[RAG Response] ========== RAG RESPONSE GENERATION COMPLETE ==========")
        
        # Build final result
        result = {
            "query": query,
            "contexts": context_texts,
            "ground_truth": ground_truth,
            "response": response
        }
        
        logger.info(f"[_process_single_query] ========== FINAL RESULT ==========")
        logger.info(f"[_process_single_query] Query: '{query[:60]}...'")
        logger.info(f"[_process_single_query] Contexts: {len(context_texts)} chunks")
        logger.info(f"[_process_single_query] Ground truth: {len(ground_truth)} chars {'✓' if ground_truth else '✗'}")
        logger.info(f"[_process_single_query] RAG response: {len(response)} chars {'✓' if response else '✗'}")
        logger.info(f"[_process_single_query] ==================================")
        
        return result


class ProcessEvalQuery:
    """Process a single query: search contexts, generate ground truth, and optionally RAG response"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Process a single query for evaluation dataset"""
        import asyncio
        
        # Handle input from parallel task
        # When parallel_inputs is used, each query string is passed directly
        generate_queries_input = self.inputs.get("generate_queries")
        
        # If it's a string, use it directly (parallel task passes individual queries)
        if isinstance(generate_queries_input, str):
            query = generate_queries_input
        # If it's a list, get first item (fallback)
        elif isinstance(generate_queries_input, list) and len(generate_queries_input) > 0:
            query = generate_queries_input[0] if isinstance(generate_queries_input[0], str) else None
        else:
            query = None
        
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        language = self.inputs.get("language", "en")
        chunks_per_query = self.inputs.get("chunks_per_query", 5)
        embedding_model = self.inputs.get("embedding_model", "text-embedding-3-large")
        embedding_provider = self.inputs.get("embedding_provider", "azure_openai")
        ground_truth_prompt_key = self.inputs.get("ground_truth_prompt_key", "generate-ground-truth")
        generate_rag_response = self.inputs.get("generate_rag_response", True)
        rag_prompt_key = self.inputs.get("rag_prompt_key", "run-vector-rag")
        
        if not query:
            return {"error": "Missing query input", "query": None}
        
        return asyncio.run(self._process_query_async(
            query=query,
            client_id=client_id,
            project_id=project_id,
            language=language,
            chunks_per_query=chunks_per_query,
            embedding_model=embedding_model,
            embedding_provider=embedding_provider,
            ground_truth_prompt_key=ground_truth_prompt_key,
            generate_rag_response=generate_rag_response,
            rag_prompt_key=rag_prompt_key
        ))
    
    async def _process_query_async(
        self,
        query: str,
        client_id: str,
        project_id: str,
        language: str,
        chunks_per_query: int,
        embedding_model: str,
        embedding_provider: str,
        ground_truth_prompt_key: str,
        generate_rag_response: bool,
        rag_prompt_key: str
    ) -> Dict[str, Any]:
        """Async method to process a single query"""
        # Step 1: Search relevant chunks
        search_inputs = {
            "input_text": query,
            "client_id": client_id,
            "project_id": project_id,
            "language": language,
            "top_k": chunks_per_query,
            "embedding_model": embedding_model,
            "embedding_provider": embedding_provider
        }
        
        search_operation = SearchRelevantChunks(
            inputs=search_inputs,
            project_name=self.project_name,
            prompt_config_src=self.prompt_config_src,
            pipeline_key="search_relevant_chunks"
        )
        
        search_result = search_operation.execute()
        relevant_chunks = search_result.get("relevant_chunks", [])
        
        if not relevant_chunks:
            return {
                "query": query,
                "error": "No contexts retrieved",
                "contexts": [],
                "ground_truth": "",
                "response": ""
            }
        
        # Step 2: Generate ground truth
        context_texts = [chunk.get("text", "") for chunk in relevant_chunks if chunk.get("text")]
        contexts_combined = "\n\n".join(context_texts[:4000])
        
        llm_gateway = LLMGateway()
        ground_truth = ""
        try:
            ground_truth_response = await llm_gateway.generate(
                prompt_key=ground_truth_prompt_key,
                variables={"query": query, "contexts": contexts_combined},
                temperature=0.3,
                max_tokens=500
            )
            ground_truth = str(ground_truth_response).strip() if ground_truth_response else ""
        except Exception as e:
            logger.warning(f"Error generating ground truth: {e}")
        
        # Step 3: Optionally generate RAG response
        response = ""
        if generate_rag_response:
            try:
                rag_response = await llm_gateway.generate(
                    prompt_key=rag_prompt_key,
                    variables={"input_text": query, "contexts": contexts_combined},
                temperature=0.7,
                max_tokens=500
            )
                response = str(rag_response).strip() if rag_response else ""
            except Exception as e:
                logger.warning(f"Error generating RAG response: {e}")
        
        return {
            "query": query,
            "contexts": context_texts,
            "ground_truth": ground_truth,
            "response": response
        }


class CombineEvalDataset:
    """Combine processed queries into final evaluation dataset"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Combine processed queries into dataset"""
        # Get processed queries from previous step (process_queries)
        process_result = self.inputs.get("process_queries", {})
        
        # Handle batch processing result format
        if isinstance(process_result, dict):
            processed_queries = process_result.get("processed_queries", [])
        elif isinstance(process_result, list):
            processed_queries = process_result
        else:
            processed_queries = []
        
        # Ensure it's a list
        if not isinstance(processed_queries, list):
            processed_queries = [processed_queries] if processed_queries else []
        
        # Flatten if nested (from parallel merge or batch processing)
        dataset = []
        for item in processed_queries:
            if isinstance(item, list):
                dataset.extend([d for d in item if isinstance(d, dict) and "query" in d])
            elif isinstance(item, dict):
                if "query" in item:
                    dataset.append(item)
                elif "response" in item:  # Could be wrapped
                    dataset.append(item)
        
        # Filter out errors
        dataset = [d for d in dataset if not d.get("error")]
        
        logger.info(f"Combined {len(dataset)} valid entries into evaluation dataset")
        
        return {
            "dataset": dataset,
            "num_queries": len(dataset)
        }


class SaveEvalDatasetToFile:
    """Save evaluation dataset to JSON file"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Save dataset to JSON file"""
        import json
        import os
        from datetime import datetime
        
        # Get dataset from combine_dataset step
        combine_result = self.inputs.get("combine_dataset", {})
        if isinstance(combine_result, dict):
            dataset = combine_result.get("dataset", [])
        else:
            dataset = []
        
        output_path = self.inputs.get("output_path", "eval_dataset.json")
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        language = self.inputs.get("language", "en")
        chunks_per_query = self.inputs.get("chunks_per_query", 5)
        
        if not dataset:
            logger.warning("No dataset to save")
            return {"status": "failed", "error": "No dataset to save"}
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path) if os.path.dirname(output_path) else "."
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # Save dataset
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)
        
        dataset_metadata = {
            "num_queries": len(dataset),
            "client_id": client_id,
            "project_id": project_id,
            "language": language,
            "generation_timestamp": datetime.utcnow().isoformat(),
            "chunks_per_query": chunks_per_query
        }
        
        logger.info(f"Successfully saved {len(dataset)} entries to {output_path}")
        
        return {
            "status": "success",
            "output_path": output_path,
            "dataset_metadata": dataset_metadata,
            "dataset": dataset  # Pass through for next step
        }


class SaveEvalDatasetToLangfuse:
    """Save evaluation dataset to Langfuse"""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, prompt_config_src: str, pipeline_key: str):
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config_src = prompt_config_src
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """Save dataset to Langfuse"""
        try:
            from langfuse import Langfuse
        except ImportError:
            logger.warning("Langfuse package not available. Skipping Langfuse dataset creation.")
            return {"status": "skipped", "reason": "Langfuse not available"}
        
        # Get dataset from save_dataset_to_file step
        save_result = self.inputs.get("save_dataset_to_file", {})
        if isinstance(save_result, dict):
            dataset = save_result.get("dataset", [])
            dataset_metadata = save_result.get("dataset_metadata", {})
        else:
            dataset = []
            dataset_metadata = {}
        
        # Get num_chunks_used from retrieve_random_chunks step
        retrieve_result = self.inputs.get("retrieve_random_chunks", {})
        if isinstance(retrieve_result, dict):
            num_chunks_used = retrieve_result.get("num_chunks", 0)
        else:
            num_chunks_used = 0
        
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        language = self.inputs.get("language", "en")
        
        if not dataset:
            logger.warning("No dataset to save to Langfuse")
            return {"status": "failed", "error": "No dataset to save"}
        
        try:
            langfuse = Langfuse()
            
            dataset_name = f"ragas_generated_testset_{client_id}_{project_id}_{language}"
            dataset_description = f"Synthetic RAG test set (RAGAS) for {client_id}/{project_id}"
            
            logger.info(f"Creating Langfuse dataset: {dataset_name}")
            langfuse.create_dataset(
                name=dataset_name,
                description=dataset_description,
                metadata={
                    "source": "RAGAS",
                    "docs_used": num_chunks_used,
                    "client_id": client_id,
                    "project_id": project_id,
                    "language": language,
                    "num_queries": len(dataset),
                    "chunks_per_query": dataset_metadata.get("chunks_per_query", 5),
                    "generation_timestamp": dataset_metadata.get("generation_timestamp", "")
                }
            )
            
            logger.info(f"Adding {len(dataset)} items to Langfuse dataset...")
            for idx, row in enumerate(dataset, 1):
                try:
                    item_metadata = {
                        "reference_contexts": row.get("contexts", []),
                        "query_index": idx,
                        "has_response": bool(row.get("response")),
                        "num_contexts": len(row.get("contexts", []))
                    }
                    
                    langfuse.create_dataset_item(
                        dataset_name=dataset_name,
                        input=row.get("query", ""),
                        expected_output=row.get("ground_truth", ""),
                        metadata=item_metadata
                    )
                    
                    if idx % 10 == 0:
                        logger.info(f"Added {idx}/{len(dataset)} items to Langfuse dataset")
                except Exception as e:
                    logger.warning(f"Error adding dataset item {idx} to Langfuse: {e}")
                    continue
            
            langfuse.flush()
            logger.info(f"Successfully added {len(dataset)} items to Langfuse dataset: {dataset_name}")
            
            return {
                "status": "success",
                "langfuse_dataset_name": dataset_name
            }
            
        except Exception as e:
            logger.error(f"Error creating Langfuse dataset: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "failed", "error": str(e)}


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
    # Evaluation dataset operations
    "retrieve_random_chunks": RetrieveRandomChunks,
    "generate_queries_from_chunks": GenerateQueriesFromChunks,
    "search_contexts_for_queries": SearchContextsForQueries,
    "process_eval_query": ProcessEvalQuery,
    "process_eval_queries_batch": ProcessEvalQueriesBatch,
    "combine_eval_dataset": CombineEvalDataset,
    "save_eval_dataset_to_file": SaveEvalDatasetToFile,
    "save_eval_dataset_to_langfuse": SaveEvalDatasetToLangfuse,
    # Utility operations
    "combine_vector_response_and_references": CombineVectorResponseAndReferences,
    "evaluate_rag_with_ragas": EvaluateRAGWithRagas,
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
