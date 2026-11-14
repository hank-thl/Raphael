"""Caller supplies a trusted owner; no public API or raw-record loading here."""
from app.repositories.content_types import normalize_name


class ClassificationService:
    def __init__(self, extractor, repository):
        self.extractor = extractor
        self.repository = repository

    def analyze(self, uid, data):
        # Replace caller data, never accept a caller-controlled category catalog.
        names = self.repository.list_names(uid)
        request = data.model_copy(update={'content_types': names})
        result = self.extractor.analyze(request)
        # Complete source validation before any category write, even for alternate extractors.
        result.validate_sources(request)
        name = normalize_name(result.content_type)
        category = self.repository.resolve(uid, name)
        return result.model_copy(update={'content_type': category['name']}), category['_id']
