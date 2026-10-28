"""AI pipeline: conversation, prompts, context, extraction, classification."""

from app.ai.context_manager import CallContext, ContextManager, Message, context_manager
from app.ai.conversation import ConversationEngine
from app.ai.conversation_state import COLLECTION_FIELDS, ConversationPhase, ConversationState
from app.ai.department_classifier import DepartmentClassifier
from app.ai.language_detector import LanguageDetector
from app.ai.lead_extractor import LeadExtractor
from app.ai.prompt_manager import PromptManager
from app.ai.summary_generator import SummaryGenerator

__all__ = [
    "COLLECTION_FIELDS",
    "CallContext",
    "ContextManager",
    "ConversationEngine",
    "ConversationPhase",
    "ConversationState",
    "DepartmentClassifier",
    "LanguageDetector",
    "LeadExtractor",
    "Message",
    "PromptManager",
    "SummaryGenerator",
    "context_manager",
]
