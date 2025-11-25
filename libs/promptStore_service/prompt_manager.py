"""
Langfuse-based Prompt Management System

This module provides centralized, agnostic prompt management using Langfuse.
It can handle any prompt using just a prompt key - no need to maintain enums or hardcoded types.

Key Features:
- Fetch any prompt from Langfuse using just the prompt key/name
- Automatic caching for performance
- Domain-specific configuration support
- Variable substitution and template compilation
- Fully agnostic - no enums or hardcoded types needed

Usage:
    # Initialize manager
    manager = LangfusePromptManager()
    
    # Get any prompt by key
    prompt = manager.get_any_prompt("my-custom-prompt", {
        "variable1": "value1",
        "variable2": "value2"
    })
    
    # No need to update enums when adding new prompts to Langfuse!
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
# enum import removed - no longer using PromptType enum
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

try:
    from langfuse import get_client, Langfuse
except ImportError as e:
    logger.error("Langfuse package not available. Install it via: pip install langfuse")
    raise ImportError("Langfuse package is required for prompt management") from e

from libs.llm_service.utils import parse_llm_json_response

# PromptType enum completely removed - system is now fully agnostic
# Use string prompt keys directly: manager.get_any_prompt("entity-extraction", variables)


@dataclass
class LangfusePromptTemplate:
    """Represents a prompt template retrieved from Langfuse"""
    name: str
    content: str
    config: Dict[str, Any]
    labels: List[str]
    version: int
    
    def compile(self, variables: Dict[str, Any]) -> str:
        """Compile the prompt template with variables"""
        try:
            return self.content.format(**variables)
        except (KeyError, IndexError, ValueError) as e:
            # Fallback: perform conservative placeholder substitution for known variables
            # without interpreting other braces (e.g., Cypher maps {name: '...'}).
            safe_text = self.content
            for key, value in variables.items():
                try:
                    safe_text = safe_text.replace("{" + key + "}", str(value))
                except Exception:
                    continue
            return safe_text
    
    def get_default_values(self) -> Dict[str, str]:
        """Get default values for common optional variables"""
        return {
            'tuple_delimiter': '|',
            'record_delimiter': '##',
            'completion_delimiter': '<|COMPLETE|>',
            'max_entities': '10',
            'language': 'English',
            'domain': 'general',
            # Prompt variables that may be absent depending on step wiring
            'subqueries': '[]',
            'relationship_tripplets': '[]',
            'schema': ''
        }


class PromptSource(ABC):
    """Abstract base class for prompt sources"""
    
    @abstractmethod
    def fetch_prompt(self, prompt_key: str, label: str = "production") -> LangfusePromptTemplate:
        pass
    
    @abstractmethod
    def get_multiple_prompts(self, prompt_keys: List[str], label: str = "production") -> Dict[str, LangfusePromptTemplate]:
        pass


class LangfusePromptSource(PromptSource):
    """Langfuse-based prompt source implementation"""
    
    def __init__(self, 
                 public_key: Optional[str] = None,
                 secret_key: Optional[str] = None,
                 host: Optional[str] = None):
        """
        Initialize Langfuse client
        
        Args:
            public_key: Langfuse public key (defaults to LANGFUSE_PUBLIC_KEY env var)
            secret_key: Langfuse secret key (defaults to LANGFUSE_SECRET_KEY env var)
            host: Langfuse host (defaults to LANGFUSE_HOST env var)
        """
        self.public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
        self.host = host or os.environ.get("LANGFUSE_HOST")
        
        if not all([self.public_key, self.secret_key, self.host]):
            missing = []
            if not self.public_key:
                missing.append("LANGFUSE_PUBLIC_KEY")
            if not self.secret_key:
                missing.append("LANGFUSE_SECRET_KEY")
            if not self.host:
                missing.append("LANGFUSE_HOST")
            
            raise ValueError(f"Missing required Langfuse configuration: {', '.join(missing)}")
        
        # Initialize Langfuse client
        self.langfuse = Langfuse(
            public_key=self.public_key,
            secret_key=self.secret_key,
            host=self.host
        )
        
        logger.info(f"Initialized Langfuse client with host: {self.host}")
    
    def fetch_prompt(self, prompt_key: str, label: str = "production") -> LangfusePromptTemplate:
        """
        Fetch a single prompt from Langfuse
        
        Args:
            prompt_key: The prompt name in Langfuse
            label: The prompt label/version to fetch (default: "production")
            
        Returns:
            LangfusePromptTemplate instance
        """
        try:
            # Get prompt by name and label
            prompt = self.langfuse.get_prompt(name=prompt_key, label=label)
            logger.info('############################ LangfusePromptSource.fetch_prompt ############################')
            logger.info(f'{prompt_key=} {label=}')
            logger.info(f'{prompt=}')
            
            if not prompt:
                raise ValueError(f"Prompt '{prompt_key}' with label '{label}' not found in Langfuse")
            
            return LangfusePromptTemplate(
                name=prompt_key,
                content=prompt.prompt,
                config=prompt.config or {},
                labels=prompt.labels or [],
                version=prompt.version or 1
            )
            
        except Exception as e:
            logger.error(f"Failed to fetch prompt '{prompt_key}' from Langfuse at {self.host}: {e}")
            if "Connection refused" in str(e):
                logger.error(f"Cannot connect to Langfuse at {self.host}. If running in Docker, ensure LANGFUSE_HOST uses 'host.docker.internal' instead of 'localhost'")
            raise RuntimeError(f"Error fetching prompt '{prompt_key}': {e}")
    
    def get_multiple_prompts(self, prompt_keys: List[str], label: str = "production") -> Dict[str, LangfusePromptTemplate]:
        """
        Fetch multiple prompts from Langfuse
        
        Args:
            prompt_keys: List of prompt names to fetch
            label: The prompt label/version to fetch (default: "production")
            
        Returns:
            Dictionary mapping prompt keys to LangfusePromptTemplate instances
        """
        prompts = {}
        for key in prompt_keys:
            try:
                prompts[key] = self.fetch_prompt(key, label)
            except Exception as e:
                logger.error(f"Failed to fetch prompt '{key}': {e}")
                # Continue with other prompts even if one fails
                continue
        
        return prompts


class LangfusePromptManager:
    """
    Centralized prompt manager using Langfuse as the source
    
    This replaces the file-based VectorRAGPromptManager with Langfuse integration.
    """
    
    def __init__(self, 
                 prompt_source: Optional[PromptSource] = None,
                 default_language: str = "en",
                 organization_name: Optional[str] = None,
                 project_name: Optional[str] = None):
        """
        Initialize the Langfuse prompt manager
        
        Args:
            prompt_source: Custom prompt source (defaults to LangfusePromptSource)
            default_language: Default language to use when fetching prompts (defaults to "en")
            organization_name: Organization name for logging/validation
            project_name: Project name for logging/validation
        """
        self.prompt_source = prompt_source or LangfusePromptSource()
        
        # Use language as label - no more "production" or "latest"
        # Default to English if not specified
        self.default_language = default_language
        
        self.organization_name = organization_name
        self.project_name = project_name
        self._cache: Dict[str, LangfusePromptTemplate] = {}
        
        logger.info(f"Initialized LangfusePromptManager (org: {organization_name}, project: {project_name}, default_language: {self.default_language})")
    
    def get_prompt(self, prompt_key: str, label: Optional[str] = None, language: Optional[str] = None) -> LangfusePromptTemplate:
        """
        Get a prompt template by key, with language-based label routing
        
        Args:
            prompt_key: The prompt key/name in Langfuse (e.g., "entity-extraction", "my-custom-prompt")
            label: Optional label override (for backward compatibility, but language takes precedence)
            language: Language code ('en' or 'it'). This is used as the label to fetch language-specific prompt.
            
        Returns:
            LangfusePromptTemplate instance
            
        Note:
            This method ONLY uses language labels ('en', 'it'). 
            No fallback to 'production' or 'latest' labels.
        """
        # Determine the language to use
        effective_language = language or self.default_language
        
        # Validate language
        if effective_language not in ["en", "it", "fr"]:
            logger.warning(f"Invalid language '{effective_language}', defaulting to '{self.default_language}'")
            effective_language = self.default_language
        
        # Use language as the label
        effective_label = effective_language
        cache_key = f"{prompt_key}:{effective_label}"
        
        # Check cache first
        if cache_key in self._cache:
            logger.info(f"Using cached prompt: {prompt_key} (label: {effective_label})")
            return self._cache[cache_key]
        
        # Fetch from Langfuse using language as label
        try:
            template = self.prompt_source.fetch_prompt(prompt_key, effective_label)
            self._cache[cache_key] = template
            logger.info(f"Fetched prompt: {prompt_key} (label: {effective_label})")
            return template
        except Exception as e:
            logger.warning(f"Failed to get prompt '{prompt_key}' with label '{effective_label}': {e}")
            
            # Fallback: try common labels if language-specific label fails
            fallback_labels = ["latest", "production"]
            for fallback_label in fallback_labels:
                try:
                    logger.info(f"Trying fallback label '{fallback_label}' for prompt '{prompt_key}'")
                    template = self.prompt_source.fetch_prompt(prompt_key, fallback_label)
                    self._cache[cache_key] = template
                    logger.info(f"Fetched prompt: {prompt_key} (fallback label: {fallback_label})")
                    return template
                except Exception as fallback_error:
                    logger.warning(f"Fallback label '{fallback_label}' also failed: {fallback_error}")
                    continue
            
            # If all attempts failed, raise error
            logger.error(f"Failed to get prompt '{prompt_key}' with label '{effective_label}' and fallback labels {fallback_labels}")
            logger.error(f"Make sure you have ingested prompts with: python scripts/ingest_langfuse_prompts.py --language {effective_label}")
            raise RuntimeError(f"Prompt '{prompt_key}' not found with label '{effective_label}' or fallback labels. Please check Langfuse connection and prompt ingestion.") from e
    
    def get_formatted_prompt(self, 
                           prompt_key: str, 
                           variables: Dict[str, Any],
                           label: Optional[str] = None,
                           domain_id: Optional[str] = None) -> str:
        """
        Get a formatted prompt with variables substituted
        
        Args:
            prompt_key: The prompt key/name in Langfuse (e.g., "entity-extraction", "my-custom-prompt")
            variables: Variables to substitute in the template
            label: Optional label override (deprecated, language is used instead)
            domain_id: Optional domain configuration (e.g., "resume", "scientific")
            
        Returns:
            Formatted prompt string
        """
        # Extract language from variables to route to correct prompt
        language = variables.get("language")
        if isinstance(language, str):
            # Normalize language to lowercase 2-letter code
            language = language.lower().strip()

            if language in ["english", "inglese", "en"]:
                language = "en"
            elif language in ["french", "français", "francais", "fr"]:
                language = "fr"
            else:
                # If invalid language, use default
                logger.warning(f"Invalid language value '{language}', using default '{self.default_language}'")
                language = self.default_language
        else:
            # If no language specified, use default
            language = self.default_language
        
        template = self.get_prompt(prompt_key, label, language)
        
        # Apply domain-specific defaults and transformations
        enhanced_variables = self._apply_domain_configuration(
            prompt_key, variables, domain_id
        )
        
        # Transform inputs to match prompt expectations
        transformed_variables = self._transform_inputs_to_prompt_format(
            prompt_key, enhanced_variables
        )
        
        # Add base default values for common optional variables
        defaults = template.get_default_values()
        final_variables = {**defaults, **transformed_variables}
        
        print('################## LangfusePromptManager.get_formatted_prompt ##################')
        print(f'{prompt_key=} {label=}')
        print(f'{template=}')
        print(f'{final_variables=}')
        
        compiled = template.compile(final_variables)
        return compiled


    def get_formatted_prompt_and_config(
        self,
        prompt_key: str,
        variables: Dict[str, Any],
        label: Optional[str] = None,
        domain_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Return both the formatted prompt text and the associated Langfuse config.

        Args:
            prompt_key: The prompt key/name in Langfuse
            variables: Variables to substitute in the template
            label: Optional label override (deprecated, language is used instead)
            domain_id: Optional domain configuration

        Returns:
            { "prompt": str, "config": Dict[str, Any] }
        """
        # Extract language from variables to route to correct prompt
        language = variables.get("language")
        if isinstance(language, str):
            # Normalize language to lowercase 2-letter code
            language = language.lower().strip()
            if language in ["english", "inglese", "en"]:
                language = "en"
            elif language in ["french", "français", "francais", "fr"]:
                language = "fr"
            else:
                # If invalid language, use default
                logger.warning(f"Invalid language value '{language}', using default '{self.default_language}'")
                language = self.default_language
        else:
            # If no language specified, use default
            language = self.default_language
        
        template = self.get_prompt(prompt_key, label, language)

        # Apply domain-specific defaults and transformations
        enhanced_variables = self._apply_domain_configuration(
            prompt_key, variables, domain_id
        )

        # Transform inputs to match prompt expectations
        transformed_variables = self._transform_inputs_to_prompt_format(
            prompt_key, enhanced_variables
        )

        # Add base default values for common optional variables
        defaults = template.get_default_values()
        final_variables = {**defaults, **transformed_variables}

        print('################## LangfusePromptManager.get_formatted_prompt_and_config ##################')
        print(f'{final_variables=}')

        compiled = template.compile(final_variables)
        return {
            "prompt": compiled,
            "config": template.config or {}
        }
    
    def get_any_prompt(self, 
                      prompt_key: str, 
                      variables: Optional[Dict[str, Any]] = None,
                      label: Optional[str] = None,
                      domain_id: Optional[str] = None) -> str:
        """
        Get any prompt from Langfuse by key with optional variable substitution
        
        This is the main method to use for any prompt - no need to maintain enums or specific methods.
        Language routing is automatic based on the 'language' variable.
        
        Args:
            prompt_key: The prompt key/name in Langfuse (e.g., "entity-extraction", "my-custom-prompt")
            variables: Optional variables to substitute in the template. If 'language' is provided,
                      will automatically route to language-specific prompt (e.g., "prompt-key-it")
            label: Optional label override (defaults to default_label)
            domain_id: Optional domain configuration (e.g., "resume", "scientific")
            
        Returns:
            Formatted prompt string (or raw template if no variables provided)
            
        Examples:
            # Get a simple prompt without variables
            prompt = manager.get_any_prompt("welcome-message")
            
            # Get a prompt with variables
            prompt = manager.get_any_prompt("entity-extraction", {
                "input_text": "Some text to analyze",
                "entity_types": "PERSON,ORGANIZATION"
            })
            
            # Get a prompt with language routing (automatically uses "resume-analysis-it")
            prompt = manager.get_any_prompt("resume-analysis", {
                "resume_text": "...",
                "language": "it"
            }, domain_id="resume")
        """
        if variables is None:
            # Just return the raw template (no language routing without variables)
            template = self.get_prompt(prompt_key, label)
            return template.content
        else:
            # Return formatted prompt (with automatic language routing)
            return self.get_formatted_prompt(prompt_key, variables, label, domain_id)
    
    def list_available_prompts(self, label: Optional[str] = None) -> List[str]:
        """
        List available prompts from Langfuse (if supported by the client)
        
        Args:
            label: Optional label filter
            
        Returns:
            List of available prompt keys
            
        Note: This method may not be available in all Langfuse client versions.
        If not available, it will return an empty list and log a warning.
        """
        try:
            # This is a hypothetical method - actual implementation depends on Langfuse client capabilities
            if hasattr(self.langfuse, 'list_prompts'):
                prompts = self.langfuse.list_prompts(label=label)
                return [p.name for p in prompts]
            else:
                logger.warning("list_prompts not available in current Langfuse client version")
                return []
        except Exception as e:
            logger.error(f"Failed to list available prompts: {e}")
            return []
    
    def prompt_exists(self, prompt_key: str, label: Optional[str] = None) -> bool:
        """
        Check if a prompt exists in Langfuse
        
        Args:
            prompt_key: The prompt key to check
            label: Optional label override
            
        Returns:
            True if prompt exists, False otherwise
        """
        try:
            self.get_prompt(prompt_key, label)
            return True
        except Exception:
            return False
    
    def _apply_domain_configuration(self, 
                                  prompt_key: str, 
                                  variables: Dict[str, Any],
                                  domain_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Apply domain-specific configuration to variables
        
        Args:
            prompt_key: The prompt key/name in Langfuse
            variables: Input variables
            domain_id: Optional domain ID (e.g., "resume", "scientific")
            
        Returns:
            Enhanced variables with domain-specific defaults
        """
        enhanced_variables = variables.copy()
        pipeline_key = prompt_key
        
        # Import base defaults
        try:
            from libs.promptStore_service.domain_configs.base_defaults import BASE_DEFAULTS_MAP
            defaults_map = BASE_DEFAULTS_MAP
        except ImportError as e:
            logger.warning(f"Could not import base defaults: {e}, using empty defaults")
            defaults_map = {}
        
        # Apply base defaults for this pipeline_key
        if pipeline_key in defaults_map:
            for key, default_value in defaults_map[pipeline_key].items():
                if key not in enhanced_variables:
                    enhanced_variables[key] = default_value
                    logger.debug(f"Applied base default {key} for {pipeline_key}")
        
        # Apply domain-specific overrides if domain_id is provided
        if domain_id:
            domain_config = self._get_domain_config(domain_id)
            if domain_config and pipeline_key in domain_config:
                for key, domain_value in domain_config[pipeline_key].items():
                    enhanced_variables[key] = domain_value
                    logger.debug(f"Applied domain override {key} for {pipeline_key} (domain: {domain_id})")
            # Expose domain_id downstream for transforms that may need it
            enhanced_variables["domain_id"] = domain_id
        
        return enhanced_variables
    
    def _get_domain_config(self, domain_id: str) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Get domain configuration by domain ID
        
        Args:
            domain_id: Domain identifier
            
        Returns:
            Domain configuration dictionary or None if not found
        """
        domain_configs = {
            "resume": "libs.promptStore_service.domain_configs.resume_domain.RESUME_DOMAIN_CONFIG",
            "general": "libs.promptStore_service.domain_configs.general_domain.GENERAL_DOMAIN_CONFIG",
            "scientific": "libs.promptStore_service.domain_configs.scientific_domain.SCIENTIFIC_DOMAIN_CONFIG",
            "legal": "libs.promptStore_service.domain_configs.legal_domain.LEGAL_DOMAIN_CONFIG",
            "financial": "libs.promptStore_service.domain_configs.financial_domain.FINANCIAL_DOMAIN_CONFIG",
            "news": "libs.promptStore_service.domain_configs.news_domain.NEWS_DOMAIN_CONFIG",
            # Backward-compatible keys
            "dpac": "libs.promptStore_service.domain_configs.dpac_domain.DPAC_DOMAIN_CONFIG",
            "dpac_it": "libs.promptStore_service.domain_configs.it_dpac_domain.IT_DPAC_DOMAIN_CONFIG",
            # New explicit language keys
            "en_dpac": "libs.promptStore_service.domain_configs.en_dpac_domain.EN_DPAC_DOMAIN_CONFIG",
            "it_dpac": "libs.promptStore_service.domain_configs.it_dpac_domain.IT_DPAC_DOMAIN_CONFIG",
            "fr_dpac": "libs.promptStore_service.domain_configs.fr_dpac_domain.FR_DPAC_DOMAIN_CONFIG",
        }
        
        if domain_id not in domain_configs:
            logger.warning(f"Unknown domain_id '{domain_id}'. Available domains: {list(domain_configs.keys())}")
            return None
        
        try:
            module_path, config_name = domain_configs[domain_id].rsplit('.', 1)
            module = __import__(module_path, fromlist=[config_name])
            return getattr(module, config_name)
        except Exception as e:
            logger.error(f"Failed to load domain config for '{domain_id}': {e}")
            return None

    def _transform_inputs_to_prompt_format(self, 
                                         prompt_key: str, 
                                         variables: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform pipeline inputs to match prompt template expectations
        
        Args:
            prompt_key: The prompt key/name in Langfuse
            variables: Input variables
            
        Returns:
            Transformed variables
        """
        pipeline_key = prompt_key
        transformed = variables.copy()
        logger.info('############################## LangfusePromptManager._transform_inputs_to_prompt_format ###############################')
        logger.info(f'{transformed=}')
        
        if pipeline_key == "run-vector-rag":
            # Transform inputs for naive RAG inference
            # Get relevant chunks from search_relevant_chunks
            search_results = transformed.get("search_relevant_chunks", {})
            if isinstance(search_results, dict):
                # The chunks are directly in the top level, not nested under 'response'
                chunks = search_results.get("relevant_chunks", [])
                if isinstance(chunks, list) and chunks:
                    # Format chunks as relevant_chunks for the prompt
                    relevant_chunks_text = "Relevant Context:\n\n"
                    for i, chunk in enumerate(chunks, 1):
                        if isinstance(chunk, dict):
                            text = chunk.get("text", "")
                            if text:
                                relevant_chunks_text += f"- {text}\n\n"
                    transformed["relevant_chunks"] = relevant_chunks_text.strip()
                    logger.debug(f"Transformed search_relevant_chunks to relevant_chunks ({len(relevant_chunks_text)} chars)")
                else:
                    transformed["relevant_chunks"] = "No relevant context found."
                    logger.warning("No chunks found in search_relevant_chunks")
            else:
                transformed["relevant_chunks"] = "No relevant context found."
                logger.warning("search_relevant_chunks is not a valid dict")

            # Get chat history from fetch_chat_history
            chat_history = transformed.get("fetch_chat_history", {})
            if isinstance(chat_history, dict):
                # The messages are directly in the top level, not nested under 'response'
                messages = chat_history.get("chat_history", [])
                if isinstance(messages, list) and messages:
                    # Format chat history for the prompt
                    history_text = "Chat History:\n\n"
                    for msg in messages:
                        if isinstance(msg, dict):
                            role = msg.get("role", "unknown")
                            content = msg.get("content", "")
                            timestamp = msg.get("created_at", msg.get("timestamp", ""))
                            if content:
                                history_text += f"{role.capitalize()}: {content}\n"
                                if timestamp:
                                    history_text += f"  (at {timestamp})\n"
                    transformed["chat_history"] = history_text.strip()
                    logger.debug(f"Transformed fetch_chat_history to chat_history ({len(history_text)} chars)")
                else:
                    transformed["chat_history"] = "No previous conversation history."
                    logger.debug("No messages found in fetch_chat_history")
            else:
                transformed["chat_history"] = "No previous conversation history."
                logger.warning("fetch_chat_history is not a valid dict")

            # Get user facts from fetch_user_facts
            user_facts = transformed.get("fetch_user_facts", {})
            if isinstance(user_facts, dict):
                # The facts are directly in the top level, not nested under 'response'
                facts = user_facts.get("results", [])
                if isinstance(facts, list) and facts:
                    # Format user facts for the prompt
                    facts_text = "User Facts:\n\n"
                    for fact in facts:
                        if isinstance(fact, dict) and "memory" in fact:
                            memory = fact["memory"]
                            if isinstance(memory, str) and memory.strip():
                                facts_text += f"- {memory}\n"
                    transformed["extract_user_facts"] = facts_text.strip()
                    logger.debug(f"Transformed fetch_user_facts to extract_user_facts ({len(facts_text)} chars)")
                else:
                    transformed["extract_user_facts"] = "No user facts available."
                    logger.debug("No facts found in fetch_user_facts")
            else:
                transformed["extract_user_facts"] = "No user facts available."
                logger.warning("fetch_user_facts is not a valid dict")
        #elif pipeline_key == "pipeline_key in yaml file, same as prompt in langfuse":

        return transformed

    def clear_cache(self):
        """Clear the prompt cache"""
        self._cache.clear()
        logger.info("Cleared prompt cache")
    
    def preload_prompts(self, prompt_keys: List[str], label: Optional[str] = None):
        """
        Preload multiple prompts into cache
        
        Args:
            prompt_keys: List of prompt keys/names to preload from Langfuse
            label: Optional label override
        """
        effective_label = label or self.default_label
        
        # Fetch multiple prompts
        try:
            templates = self.prompt_source.get_multiple_prompts(prompt_keys, effective_label)
            
            # Add to cache
            for key, template in templates.items():
                cache_key = f"{key}:{effective_label}"
                self._cache[cache_key] = template
            
            logger.info(f"Preloaded {len(templates)} prompts into cache")
            
        except Exception as e:
            logger.error(f"Failed to preload prompts: {e}")
            raise


# Global instance for easy access
_default_langfuse_prompt_manager: Optional[LangfusePromptManager] = None


def get_default_langfuse_prompt_manager() -> LangfusePromptManager:
    """Get the default global Langfuse prompt manager instance"""
    global _default_langfuse_prompt_manager
    if _default_langfuse_prompt_manager is None:
        _default_langfuse_prompt_manager = LangfusePromptManager(default_language="en")
    return _default_langfuse_prompt_manager


def get_langfuse_prompt_manager_from_config(prompt_config: Dict[str, Any]) -> LangfusePromptManager:
    """Create a Langfuse prompt manager from configuration"""
    return LangfusePromptManager(
        organization_name=prompt_config.get("organization_name"),
        project_name=prompt_config.get("project_name")
    )
