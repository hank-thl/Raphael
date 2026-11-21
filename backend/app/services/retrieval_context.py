"""Validate prior dialogue before using it as query context; never infer personal facts here."""
import json

from app.schemas.memory_answer import MemoryAnswer
from app.services.embeddings import MAX_INPUT_BYTES
from app.services.memory_answer import MemoryAnswerService


def retrieval_context(uid, question, previous, memories):
    dependencies = {row['_id']: MemoryAnswerService.dependency(row)['fingerprint'] for row in memories}
    context, retained, cited_ids = [], {}, []
    for turn in previous[:3]:
        if turn.get('user_uid') != uid or turn.get('state') != 'succeeded':
            continue
        try:
            old_inputs = turn['inputs']
            if not isinstance(old_inputs, list) or not all(
                    isinstance(item, dict) and item.get('memory_id') in dependencies
                    and dependencies[item['memory_id']] == item.get('fingerprint') for item in old_inputs):
                continue
            old_answer = MemoryAnswer.model_validate(turn['answer'])
            old_answer.validate_sources(memories)
            text = turn['question']
            if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                continue
            references = list(dict.fromkeys(source.memory_id for passage in old_answer.passages for source in passage.sources))
            # Every cited memory must also be a tracked input of that old answer.
            old_ids = {item['memory_id'] for item in old_inputs}
            if not set(references) <= old_ids:
                continue
            retained.update({item['memory_id']: item['fingerprint'] for item in old_inputs})
            cited_ids.extend(mid for mid in references if mid not in cited_ids)
            # Only actual cited sources need raw Memory content in the new prompt.
            # All original dependencies remain tracked separately in the new result.
            context.append({**turn, 'answer': old_answer.model_dump(),
                'inputs': [{'memory_id': mid, 'fingerprint': dependencies[mid]} for mid in references]})
        except (KeyError, TypeError, ValueError):
            continue
    query = question
    if context:
        query = json.dumps({'current_question': question, 'previous_question': context[0]['question']}, ensure_ascii=False)
    return {'previous': context, 'dependency_fingerprints': retained, 'cited_ids': cited_ids,
            'query': query, 'query_fits': len(query.encode('utf-8')) <= MAX_INPUT_BYTES}
