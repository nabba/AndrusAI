# Structured Output Validation

## Problem
- LLM outputs may not match expected formats
- Scraped data is often messy or incomplete
- Downstream processing fails on unexpected data
- No systematic validation between pipeline stages

## Solution: Pydantic Models for Validation

### Basic Model Definition
```python
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime

class Article(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    url: str = Field(..., pattern=r'^https?://')
    author: Optional[str] = None
    publish_date: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    word_count: int = Field(ge=0)
    
    @validator('title')
    def title_must_be_clean(cls, v):
        return v.strip()
```

### Parsing LLM JSON Output
```python
import json
from pydantic import ValidationError

def parse_llm_output(raw_text: str, model_class):
    """Parse and validate LLM JSON output."""
    try:
        # Extract JSON if wrapped in markdown
        if '```json' in raw_text:
            raw_text = raw_text.split('```json')[1].split('```')[0]
        elif '```' in raw_text:
            raw_text = raw_text.split('```')[1].split('```')[0]
        
        data = json.loads(raw_text.strip())
        return model_class(**data)
    except json.JSONDecodeError as e:
        raise ValueError(f'Invalid JSON: {e}')
    except ValidationError as e:
        raise ValueError(f'Validation failed: {e}')

# Usage
raw_llm_output = '''```json
{"title": "AI News", "url": "https://example.com", "word_count": 500}
```'''
article = parse_llm_output(raw_llm_output, Article)
print(article.title)  # 'AI News'
```

### Handling Missing/Invalid Data
```python
from pydantic import BaseModel, validator
from typing import Union

class FlexibleModel(BaseModel):
    class Config:
        extra = 'ignore'  # Ignore unknown fields
        
    # Coerce types automatically
    count: int
    price: float
    
    @validator('count', pre=True)
    def parse_count(cls, v):
        if isinstance(v, str):
            return int(v.replace(',', ''))
        return v
```

### Batch Validation with Error Collection
```python
def validate_batch(items: list, model_class) -> dict:
    """Validate batch, collecting successes and errors."""
    results = {'valid': [], 'invalid': []}
    
    for i, item in enumerate(items):
        try:
            validated = model_class(**item)
            results['valid'].append(validated)
        except ValidationError as e:
            results['invalid'].append({
                'index': i,
                'data': item,
                'errors': e.errors()
            })
    
    return results
```

### Transforming Between Formats
```python
from dataclasses import asdict

def to_csv_rows(validated_items: list) -> str:
    """Convert validated models to CSV."""
    if not validated_items:
        return ''
    
    import csv
    import io
    
    output = io.StringIO()
    fieldnames = validated_items[0].model_fields.keys()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for item in validated_items:
        writer.writerow(item.model_dump())
    
    return output.getvalue()
```

### JSON Schema for External Validation
```python
# Generate JSON schema for documentation or external validation
schema = Article.model_json_schema()
print(json.dumps(schema, indent=2))
```

## Validation Checklist
- [ ] Define strict types for all fields
- [ ] Add Field constraints (min_length, max_length, pattern, ge, le)
- [ ] Use Optional[] for nullable fields
- [ ] Add @validator for custom transformations
- [ ] Handle extra fields (ignore, forbid, allow)
- [ ] Test with edge cases (empty, None, malformed)
- [ ] Generate schema for documentation
