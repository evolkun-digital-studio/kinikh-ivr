from __future__ import annotations

from app.rag.index import get_knowledge_index
from app.core.config import get_settings


def stt_vocabulary_prompt() -> str:
    """Short context passed to speech-to-text as its `prompt`.

    Transcription models use the prompt as a spelling/vocabulary hint: the
    company name and service names get recognised instead of mis-heard
    ("Kinikh" -> "kinetic", "renovation" -> "innovation"), and it signals
    that the audio is a Hindi/English phone call.
    """
    settings = get_settings()
    company = settings.company_name or "Kinikh"
    index = get_knowledge_index()
    
    sections = []
    if index and index.chunks:
        # Collect unique section headings
        unique_sections = []
        seen = set()
        for chunk in index.chunks:
            if chunk.section not in seen:
                seen.add(chunk.section)
                unique_sections.append(chunk.section)
        sections = unique_sections[:15]
        
    terms = f"{company}"
    if sections:
        terms += ", " + ", ".join(sections)
        
    prompt = (
        f"Phone call to {company}. The caller may speak English, Hindi or Hinglish. "
        f"Terms: {terms}, email, phone number, quotation, site visit."
    )
    return prompt[:600]
