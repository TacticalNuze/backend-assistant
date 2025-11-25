"""
RAGAS Evaluation Service Interface

This module provides a high-level interface for RAG evaluation using RAGAS metrics
with Langfuse tracing integration.
"""
import os
import logging
import inspect
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

try:
    from langfuse import Langfuse
    from openai import AsyncAzureOpenAI
    from ragas.embeddings.base import embedding_factory
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecision, ContextRecall, ContextRelevance
    RAGAS_AVAILABLE = True
except ImportError as e:
    RAGAS_AVAILABLE = False
    IMPORT_ERROR = str(e)

load_dotenv()

logger = logging.getLogger(__name__)


class RagasEvaluationService:
    """
    High-level interface for RAG evaluation using RAGAS metrics
    
    This class provides a unified interface for evaluating RAG responses using
    RAGAS metrics (ContextPrecision, ContextRecall, ContextRelevance) with
    Langfuse tracing integration.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        azure_endpoint: Optional[str] = None,
        azure_deployment: Optional[str] = None,
        api_version: Optional[str] = None,
        embedding_model: Optional[str] = None,
        llm_model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        enable_langfuse: bool = True,
        **kwargs
    ):
        """
        Initialize the RAGAS evaluation service
        
        Args:
            api_key: Azure OpenAI API key (defaults to AZURE_OPENAI_API_KEY env var)
            azure_endpoint: Azure OpenAI endpoint (defaults to AZURE_OPENAI_ENDPOINT env var)
            azure_deployment: Azure OpenAI deployment name (defaults to AZURE_OPENAI_CHAT_DEPLOYMENT env var)
            api_version: Azure OpenAI API version (defaults to OPENAI_API_VERSION env var)
            embedding_model: Embedding model name (defaults to "text-embedding-3-large")
            llm_model: LLM model name (defaults to "gpt-4")
            max_tokens: Maximum tokens for LLM responses (defaults to MAX_TOKENS env var or 1000)
            enable_langfuse: Whether to enable Langfuse tracing (defaults to True)
            **kwargs: Additional configuration options
        """
        if not RAGAS_AVAILABLE:
            raise ImportError(
                f"RAGAS is not available. Please install ragas package. "
                f"Original error: {IMPORT_ERROR}"
            )
        
        # Load configuration from environment or parameters
        self.api_key = api_key or os.getenv("AZURE_OPENAI_API_KEY")
        self.azure_endpoint = azure_endpoint or os.getenv("AZURE_OPENAI_ENDPOINT")
        self.azure_deployment = azure_deployment or os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
        self.api_version = api_version or os.getenv("OPENAI_API_VERSION", "2024-02-15-preview")
        self.embedding_model = embedding_model or "text-embedding-3-large"
        self.llm_model = llm_model or "gpt-4"
        self.max_tokens = max_tokens or int(os.getenv("MAX_TOKENS", "1000"))
        self.enable_langfuse = enable_langfuse
        
        # Validate required configuration
        if not self.api_key or not self.azure_endpoint:
            raise ValueError("Azure OpenAI API key and endpoint are required")
        
        # Apply Azure OpenAI patch for max_completion_tokens
        self._patch_ragas_for_azure_openai()
        
        # Initialize OpenAI client
        self.openai_client = AsyncAzureOpenAI(
            azure_deployment=self.azure_deployment,
            api_key=self.api_key,
            azure_endpoint=self.azure_endpoint,
            api_version=self.api_version,
        )
        
        # Initialize RAGAS LLM and embeddings
        self.ragas_llm = llm_factory(
            self.llm_model,
            client=self.openai_client,
            max_tokens=self.max_tokens
        )
        self.ragas_embedding = embedding_factory(
            client=self.openai_client,
            model=self.embedding_model,
            interface="modern"
        )
        
        # Initialize metrics
        self.metric_classes = [ContextPrecision, ContextRecall, ContextRelevance]
        self.metrics = self._init_ragas_metrics(
            self.metric_classes,
            self.ragas_llm,
            self.ragas_embedding
        )
        
        # Initialize Langfuse if enabled
        self.langfuse = Langfuse() if self.enable_langfuse else None
        
        logger.info(
            f"Initialized RagasEvaluationService with model={self.llm_model}, "
            f"embedding={self.embedding_model}, max_tokens={self.max_tokens}, "
            f"langfuse={'enabled' if self.enable_langfuse else 'disabled'}"
        )
    
    def _patch_ragas_for_azure_openai(self):
        """
        Patch ragas' _map_openai_params to always convert max_tokens to max_completion_tokens
        for Azure OpenAI clients, since Azure requires max_completion_tokens for newer models.
        """
        from ragas.llms.base import InstructorLLM
        
        # Only patch if not already patched
        if hasattr(InstructorLLM._map_openai_params, '_patched'):
            return
        
        # Store the original method
        original_map_openai_params = InstructorLLM._map_openai_params
        
        def patched_map_openai_params(self):
            """Patched version that always converts max_tokens for Azure OpenAI"""
            mapped_args = original_map_openai_params(self)
            
            # Check if this is an Azure OpenAI client
            is_azure = hasattr(self.client, 'azure_endpoint') and self.client.azure_endpoint is not None
            
            # For Azure OpenAI, always convert max_tokens to max_completion_tokens
            if is_azure and "max_tokens" in mapped_args:
                mapped_args["max_completion_tokens"] = mapped_args.pop("max_tokens")
            
            return mapped_args
        
        # Mark as patched
        patched_map_openai_params._patched = True
        
        # Apply the patch
        InstructorLLM._map_openai_params = patched_map_openai_params
    
    def _init_ragas_metrics(self, metric_classes, llm, embedding):
        """
        Initialize metric instances with required dependencies.
        
        Args:
            metric_classes: List of metric classes to instantiate
            llm: LLM instance for metrics that need it
            embedding: Embedding instance for metrics that need it
            
        Returns:
            List of initialized metric instances
        """
        initialized_metrics = []
        
        for metric_class in metric_classes:
            # Check what dependencies the metric needs
            init_signature = inspect.signature(metric_class.__init__)
            init_params = init_signature.parameters
            
            # Build kwargs for initialization
            init_kwargs = {}
            
            # Check if metric needs LLM
            if 'llm' in init_params:
                init_kwargs['llm'] = llm
            
            # Check if metric needs embeddings
            if 'embeddings' in init_params:
                init_kwargs['embeddings'] = embedding
            
            # Instantiate the metric
            metric_instance = metric_class(**init_kwargs)
            initialized_metrics.append(metric_instance)
            
            logger.debug(
                f"Initialized {metric_class.__name__} with dependencies: {list(init_kwargs.keys())}"
            )
        
        return initialized_metrics
    
    def map_rag_output_to_score_fields(self, rag_output: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map RAG system output or dataset row to standardized fields for scoring.
        
        This function handles various field name variations and data formats commonly
        found in RAG systems and evaluation datasets.
        
        Args:
            rag_output: Dictionary containing RAG system output or dataset row with fields like:
                - Query fields: 'query', 'question', 'user_input', 'input', 'prompt'
                - Answer fields: 'answer', 'response', 'output', 'generated_answer'
                - Context fields: 'contexts', 'retrieved_contexts', 'chunks', 'context', 
                                 'documents', 'retrieved_docs'
                - Reference fields: 'reference', 'ground_truth', 'expected_answer', 'correct_answer'
        
        Returns:
            Dictionary with standardized keys:
                - 'user_input': str - The query/question to the RAG system
                - 'response': str - The RAG system's generated response
                - 'retrieved_contexts': List[str] - Retrieved context chunks
                - 'reference': str - The predefined correct answer (ground truth), optional
        """
        # Field name mappings - maps common variations to standardized names
        query_fields = ['user_input', 'query', 'question', 'input', 'prompt', 'user_query']
        answer_fields = ['response', 'answer', 'output', 'generated_answer', 'generated_response']
        context_fields = ['retrieved_contexts', 'contexts', 'chunks', 'context', 
                         'documents', 'retrieved_docs', 'retrieved_documents']
        reference_fields = ['reference', 'ground_truth', 'expected_answer', 'correct_answer', 'ground_truth_answer']
        
        # Extract user_input (query)
        user_input = None
        for field in query_fields:
            if field in rag_output and rag_output[field]:
                user_input = rag_output[field]
                break
        
        if user_input is None:
            raise ValueError(
                f"Could not find user_input/query field. Tried: {query_fields}. "
                f"Available keys: {list(rag_output.keys())}"
            )
        
        # Ensure user_input is a string
        if not isinstance(user_input, str):
            user_input = str(user_input)
        
        # Extract response (answer)
        response = None
        for field in answer_fields:
            if field in rag_output and rag_output[field]:
                response = rag_output[field]
                break
        
        if response is None:
            raise ValueError(
                f"Could not find response/answer field. Tried: {answer_fields}. "
                f"Available keys: {list(rag_output.keys())}"
            )
        
        # Ensure response is a string
        if not isinstance(response, str):
            response = str(response)
        
        # Extract retrieved_contexts
        retrieved_contexts = None
        for field in context_fields:
            if field in rag_output and rag_output[field]:
                retrieved_contexts = rag_output[field]
                break
        
        if retrieved_contexts is None:
            # If no contexts found, use empty list
            retrieved_contexts = []
        else:
            # Normalize retrieved_contexts to List[str]
            if isinstance(retrieved_contexts, str):
                # Single string context - convert to list
                retrieved_contexts = [retrieved_contexts]
            elif isinstance(retrieved_contexts, list):
                # Already a list - ensure all items are strings
                retrieved_contexts = [
                    str(item) if not isinstance(item, str) else item 
                    for item in retrieved_contexts
                ]
            else:
                # Try to convert to list
                retrieved_contexts = [str(retrieved_contexts)]
        
        # Extract reference (ground truth) - optional
        reference = None
        for field in reference_fields:
            if field in rag_output and rag_output[field]:
                reference = rag_output[field]
                break
        
        # Ensure reference is a string if it exists
        if reference is not None and not isinstance(reference, str):
            reference = str(reference)
        
        result = {
            'user_input': user_input,
            'response': response,
            'retrieved_contexts': retrieved_contexts,
        }
        
        # Only include reference if it exists
        if reference is not None:
            result['reference'] = reference
        
        return result
    
    async def score(
        self,
        user_input: str,
        retrieved_contexts: List[str],
        response: str,
        reference: Optional[str] = None
    ) -> Dict[str, float]:
        """
        Score a RAG response using RAGAS metrics with Langfuse tracing.
        
        Args:
            user_input: The query/question to the RAG system
            retrieved_contexts: Retrieved context chunks
            response: The RAG system's generated response
            reference: The predefined correct answer (ground truth), optional
        
        Returns:
            Dictionary of metric names and their scores
        """
        scores = {}
        
        # Create a span for the overall evaluation if Langfuse is enabled
        langfuse_context = (
            self.langfuse.start_as_current_observation(as_type="span", name="scoring_RAGAS_metrics")
            if self.langfuse else None
        )
        
        if langfuse_context:
            span = langfuse_context.__enter__()
            span_input = {
                "user_input": user_input,
                "retrieved_contexts": retrieved_contexts,
                "response": response,
                "num_contexts": len(retrieved_contexts)
            }
            if reference is not None:
                span_input["reference"] = reference
            span.update(input=span_input)
        else:
            span = None
        
        try:
            # Calculate each metric with individual spans
            for metric in self.metrics:
                metric_name = type(metric).__name__
                logger.debug(f"Calculating {metric_name}...")
                
                # Create a span for each metric if Langfuse is enabled
                metric_context = (
                    self.langfuse.start_as_current_observation(as_type="span", name=f"scoring_{metric_name}")
                    if self.langfuse else None
                )
                
                if metric_context:
                    metric_span = metric_context.__enter__()
                    metric_span.update(input={"metric_name": metric_name})
                else:
                    metric_span = None
                
                try:
                    # Calculate the metric score
                    if metric_name == "ContextPrecision":
                        score_result = await metric.ascore(
                            user_input=user_input,
                            reference=reference,
                            retrieved_contexts=retrieved_contexts,
                        )
                    elif metric_name == "ContextRecall":
                        score_result = await metric.ascore(
                            user_input=user_input,
                            reference=reference,
                            retrieved_contexts=retrieved_contexts,
                        )
                    elif metric_name == "ContextRelevance":
                        score_result = await metric.ascore(
                            user_input=user_input,
                            retrieved_contexts=retrieved_contexts,
                        )
                    else:
                        score_result = await metric.ascore(
                            user_input=user_input,
                            response=response,
                            retrieved_contexts=retrieved_contexts,
                        )
                    
                    score_value = score_result.value if hasattr(score_result, 'value') else score_result
                    scores[metric_name] = score_value
                    
                    # Record the score in the span if Langfuse is enabled
                    if metric_span:
                        metric_span.update(
                            output={"score": score_value},
                            metadata={
                                "metric_name": metric_name,
                                "score_type": type(score_result).__name__
                            }
                        )
                    
                    logger.debug(f"{metric_name}: {score_value}")
                    
                except ValueError as e:
                    logger.warning(
                        f"Error calculating {metric_name}: {e}, "
                        f"the correct arguments are: {list(inspect.signature(metric.score).parameters.keys())}"
                    )
                    scores[metric_name] = None
                except Exception as e:
                    logger.error(f"Error calculating {metric_name}: {e}")
                    scores[metric_name] = None
                finally:
                    if metric_context:
                        metric_context.__exit__(None, None, None)
            
            # End the evaluation span with all scores if Langfuse is enabled
            if span:
                span.update(
                    output=scores,
                    metadata={
                        "num_metrics": len(self.metrics),
                        "metrics_calculated": list(scores.keys())
                    }
                )
        
        finally:
            if langfuse_context:
                langfuse_context.__exit__(None, None, None)
        
        return scores
    
    async def evaluate(
        self,
        rag_output: Dict[str, Any],
        trace_name: str = "ragas_evaluation"
    ) -> Dict[str, Any]:
        """
        Evaluate a RAG output using Ragas metrics and trace it in Langfuse.
        
        Args:
            rag_output: Dictionary containing user_input, retrieved_contexts, response, and optionally reference
            trace_name: Name for the Langfuse trace
        
        Returns:
            Dictionary with evaluation results including:
                - user_input: The query/question
                - response: The RAG system's response
                - retrieved_contexts: Retrieved context chunks
                - reference: The predefined correct answer (if available)
                - scores: Dictionary of metric scores
                - trace_id: Langfuse trace ID (if Langfuse is enabled)
        """
        # Map dataset row to standardized fields
        mapped_data = self.map_rag_output_to_score_fields(rag_output)
        user_input = mapped_data['user_input']
        retrieved_contexts = mapped_data['retrieved_contexts']
        response = mapped_data['response']
        reference = mapped_data.get('reference')  # Optional field
        
        # Create a trace in Langfuse if enabled
        langfuse_context = (
            self.langfuse.start_as_current_observation(as_type="span", name=trace_name)
            if self.langfuse else None
        )
        
        if langfuse_context:
            span = langfuse_context.__enter__()
            span_input = {
                "user_input": user_input,
                "retrieved_contexts": retrieved_contexts,
                "response": response,
            }
            if reference is not None:
                span_input["reference"] = reference
            span.update(
                input=span_input,
                metadata={
                    "evaluation_type": "ragas",
                    "metrics": [type(m).__name__ for m in self.metrics]
                }
            )
        else:
            span = None
        
        try:
            # Calculate scores with tracing
            scores = await self.score(user_input, retrieved_contexts, response, reference)
            
            # Update trace with final results if Langfuse is enabled
            if span:
                span.update_trace(
                    output=scores,
                    metadata={"num_metrics": len(scores)}
                )
            
            result = {
                "user_input": user_input,
                "response": response,
                "retrieved_contexts": retrieved_contexts,
                "scores": scores,
            }
            
            if reference is not None:
                result["reference"] = reference
            
            if span:
                result["trace_id"] = span.id
            
            return result
            
        except Exception as e:
            # Handle errors
            if span:
                span.update_trace(
                    output={"error": str(e)},
                    tags=["ERROR"]
                )
            raise
        finally:
            if langfuse_context:
                langfuse_context.__exit__(None, None, None)
    
    def flush(self):
        """Flush Langfuse traces to ensure all data is sent."""
        if self.langfuse:
            self.langfuse.flush()

