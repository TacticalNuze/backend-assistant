"""
RAGAS Evaluation with Langfuse Tracing

This module provides backward-compatible functions for RAG evaluation using RAGAS.
It uses the RagasEvaluationService internally for consistency with other services.
"""
import asyncio
import logging
from typing import Dict, List, Any, Optional

from .service import RagasEvaluationService

logger = logging.getLogger(__name__)

# Global service instance (lazy initialization)
_service_instance: Optional[RagasEvaluationService] = None


def _get_service() -> RagasEvaluationService:
    """Get or create the global service instance."""
    global _service_instance
    if _service_instance is None:
        _service_instance = RagasEvaluationService()
    return _service_instance


def map_rag_output_to_score_fields(rag_output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map RAG system output or dataset row to standardized fields for score_with_ragas.
    
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
    
    Examples:
        >>> # From dataset
        >>> row = {"user_input": "What is AI?", "retrieved_contexts": ["AI is..."], "response": "AI is...", "reference": "AI stands for..."}
        >>> mapped = map_rag_output_to_score_fields(row)
        >>> # Returns: {"user_input": "What is AI?", "retrieved_contexts": ["AI is..."], "response": "AI is...", "reference": "AI stands for..."}
    """
    service = _get_service()
    return service.map_rag_output_to_score_fields(rag_output)


async def score_with_ragas(
    user_input: str,
    retrieved_contexts: List[str],
    response: str,
    reference: Optional[str] = None
) -> Dict[str, float]:
    """
    Score a RAG response using Ragas metrics with Langfuse tracing.
    
    Args:
        user_input: The query/question to the RAG system
        retrieved_contexts: Retrieved context chunks
        response: The RAG system's generated response
        reference: The predefined correct answer (ground truth), optional
    
    Returns:
        Dictionary of metric names and their scores
    """
    service = _get_service()
    return await service.score(user_input, retrieved_contexts, response, reference)


async def evaluate_with_langfuse(
    row: Dict[str, Any],
    trace_name: str = "ragas_evaluation"
) -> Dict[str, Any]:
    """
    Evaluate a single row with Ragas metrics and trace it in Langfuse.
    
    Args:
        row: Dictionary containing user_input, retrieved_contexts, response, and optionally reference
        trace_name: Name for the Langfuse trace
    
    Returns:
        Dictionary with evaluation results including:
            - user_input: The query/question
            - response: The RAG system's response
            - retrieved_contexts: Retrieved context chunks
            - reference: The predefined correct answer (if available)
            - scores: Dictionary of metric scores
            - trace_id: Langfuse trace ID
    """
    service = _get_service()
    return await service.evaluate(row, trace_name)


# Example usage for testing
async def main():
    """Main function to run the evaluation (for testing purposes)."""
    from datasets import load_dataset  # pyright: ignore[reportMissingImports]
    
    try:
        amnesty_qa = load_dataset("parquet", data_files="./huggingface_data/eval.parquet")["train"]
        
        # Get first row from dataset
        row = amnesty_qa[0]
        
        # Map the row to standardized fields
        mapped_data = map_rag_output_to_score_fields(row)
        user_input = mapped_data['user_input']
        response = mapped_data['response']
        retrieved_contexts = mapped_data['retrieved_contexts']
        reference = mapped_data.get('reference')
        
        print("=" * 60)
        print("RAGAS Evaluation with Langfuse Tracing")
        print("=" * 60)
        print(f"\nUser Input: {user_input}")
        print(f"\nResponse: {response}")
        print(f"\nNumber of contexts: {len(retrieved_contexts)}")
        if reference:
            print(f"\nReference: {reference}")
        print("\n" + "=" * 60)
        print("Calculating metrics...")
        print("=" * 60 + "\n")
        
        # Run evaluation with tracing
        result = await evaluate_with_langfuse(row, trace_name="ragas_amnesty_qa_evaluation")
        
        print("\n" + "=" * 60)
        print("Evaluation Results:")
        print("=" * 60)
        for metric_name, score in result["scores"].items():
            print(f"  {metric_name}: {score}")
        if "trace_id" in result:
            print(f"\nTrace ID: {result['trace_id']}")
        print("=" * 60)
        
        # Flush Langfuse to ensure all traces are sent
        service = _get_service()
        service.flush()
        
        return result
    except Exception as e:
        logger.error(f"Error in main evaluation: {e}")
        raise


if __name__ == "__main__":
    # Run the main function
    result = asyncio.run(main())
