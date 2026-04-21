# Structured Output Validation

## Purpose
Ensure all team outputs conform to defined schemas using Pydantic models for type safety, validation, and consistent formatting.

## Why This Matters

Without structured output validation:
- Research outputs have inconsistent formats
- Required fields may be missing
- Data types are unpredictable
- Downstream processing fails
- Human review is difficult

With structured validation:
- All outputs follow predictable schemas
- Automatic type coercion and validation
- Clear error messages for invalid data
- Easier aggregation and analysis
- Better integration with external systems

## Core Patterns

### 1. Define Output Schema

```python
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from datetime import datetime

# Research Finding Schema
class ResearchFinding(BaseModel):
    """Structured research finding output"""
    topic: str = Field(..., min_length=5, description="Research topic title")
    summary: str = Field(..., min_length=50, description="Executive summary")
    key_points: List[str] = Field(..., min_items=3, description="Key findings")
    sources: List[str] = Field(..., min_items=1, description="Source URLs")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0-1")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tags: List[str] = Field(default_factory=list)
    
    @validator('key_points')
    def validate_key_points(cls, v):
        return [point.strip() for point in v if len(point.strip()) > 10]

# Code Generation Output
class CodeOutput(BaseModel):
    """Structured code generation output"""
    filename: str
    language: str
    code: str
    description: str
    dependencies: List[str] = Field(default_factory=list)
    test_cases: List[str] = Field(default_factory=list)
    
# API Response Schema
class APIAnalysis(BaseModel):
    """API analysis result"""
    endpoint: str
    method: str
    status_code: int
    response_time_ms: float
    is_working: bool
    issues: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
```

### 2. Validation with Retry

```python
from typing import TypeVar, Type
import json

T = TypeVar('T', bound=BaseModel)

def validate_output(
    raw_output: str,
    model_class: Type[T],
    max_retries: int = 3
) -> T:
    """
    Validate raw output against Pydantic model with retry logic.
    
    Args:
        raw_output: Raw string output (JSON or dict)
        model_class: Pydantic model class to validate against
        max_retries: Number of validation attempts
    
    Returns:
        Validated model instance
    
    Raises:
        ValidationError: If validation fails after all retries
    """
    from pydantic import ValidationError
    
    # Parse if string
    if isinstance(raw_output, str):
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError as e:
            # Try extracting JSON from markdown code blocks
            import re
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_output)
            if json_match:
                data = json.loads(json_match.group(1))
            else:
                raise ValueError(f"Invalid JSON: {e}")
    else:
        data = raw_output
    
    # Validate
    for attempt in range(max_retries):
        try:
            return model_class(**data)
        except ValidationError as e:
            if attempt == max_retries - 1:
                raise
            # Attempt to fix common issues
            data = auto_fix_validation_errors(data, e.errors())
    
    raise ValidationError("Max retries exceeded")


def auto_fix_validation_errors(data: dict, errors: list) -> dict:
    """Attempt automatic fixes for common validation errors"""
    for error in errors:
        loc = error['loc']
        error_type = error['type']
        
        # Missing required field - add default
        if error_type == 'value_error.missing':
            field_name = loc[0]
            # Add sensible default based on field name
            if 'url' in field_name.lower():
                data[field_name] = 'https://example.com'
            elif 'id' in field_name.lower():
                data[field_name] = 'unknown'
            else:
                data[field_name] = ''
        
        # Wrong type - attempt conversion
        elif error_type == 'type_error':
            field_name = loc[0]
            expected_type = error.get('expected_type', 'str')
            current_value = data.get(field_name)
            
            if expected_type == 'integer' and isinstance(current_value, str):
                import re
                numbers = re.findall(r'\d+', current_value)
                if numbers:
                    data[field_name] = int(numbers[0])
            elif expected_type == 'array' and not isinstance(current_value, list):
                data[field_name] = [current_value]
    
    return data
```

### 3. Output Formatting

```python
def format_output(model: BaseModel, format_type: str = 'markdown') -> str:
    """Format validated output for presentation"""
    
    if format_type == 'markdown':
        return format_as_markdown(model)
    elif format_type == 'json':
        return model.json(indent=2)
    elif format_type == 'yaml':
        import yaml
        return yaml.dump(model.dict())
    else:
        return str(model.dict())


def format_as_markdown(model: BaseModel) -> str:
    """Convert Pydantic model to readable markdown"""
    lines = [f"# {model.__class__.__name__}", ""]
    
    for field_name, field_value in model:
        field_info = model.__fields__[field_name]
        description = field_info.field_info.description or ''
        
        lines.append(f"## {field_name}")
        if description:
            lines.append(f"*{description}*")
        
        if isinstance(field_value, list):
            for item in field_value:
                lines.append(f"- {item}")
        elif isinstance(field_value, dict):
            for k, v in field_value.items():
                lines.append(f"- **{k}**: {v}")
        else:
            lines.append(str(field_value))
        lines.append("")
    
    return "\n".join(lines)
```

## Standard Output Schemas

### Research Crew Outputs
```python
class ResearchReport(BaseModel):
    """Complete research report"""
    title: str
    executive_summary: str
    findings: List[ResearchFinding]
    methodology: str
    limitations: List[str]
    next_steps: List[str]
    generated_at: datetime = Field(default_factory=datetime.utcnow)

class FactCheck(BaseModel):
    """Fact-checking result"""
    claim: str
    verdict: str  # 'true', 'false', 'partial', 'unverifiable'
    evidence: List[str]
    sources: List[str]
    confidence: float
```

### Coding Crew Outputs
```python
class CodePackage(BaseModel):
    """Complete code package"""
    name: str
    description: str
    files: List[CodeOutput]
    readme: str
    requirements: List[str]
    setup_instructions: str

class TestResult(BaseModel):
    """Test execution result"""
    test_name: str
    passed: bool
    output: str
    error: Optional[str]
    duration_ms: float
```

### Writing Crew Outputs
```python
class Document(BaseModel):
    """Generated document"""
    title: str
    content: str
    word_count: int
    reading_level: str
    sections: List[str]
    metadata: dict = Field(default_factory=dict)
```

## Integration Pattern

```python
# In any crew workflow:

def process_output(raw_llm_output: str, output_type: str) -> BaseModel:
    """Process and validate output from LLM"""
    
    # Map output types to schemas
    schemas = {
        'research': ResearchReport,
        'fact_check': FactCheck,
        'code': CodePackage,
        'test': TestResult,
        'document': Document,
    }
    
    schema = schemas.get(output_type)
    if not schema:
        raise ValueError(f"Unknown output type: {output_type}")
    
    # Validate
    try:
        validated = validate_output(raw_llm_output, schema)
        return validated
    except Exception as e:
        # Log error and attempt recovery
        print(f"Validation failed: {e}")
        raise
```

## Best Practices

1. **Always Define Schemas First**: Before generating content, define the expected output schema

2. **Use Field Descriptions**: Help LLMs understand what each field should contain

3. **Set Reasonable Constraints**: Use `min_length`, `max_length`, `ge`, `le` for validation

4. **Include Metadata**: Always include timestamps, sources, and confidence scores

5. **Handle Graceful Degradation**: Use `Optional` fields for non-critical data

6. **Validate Early**: Validate outputs immediately after generation, not at end of workflow

7. **Log Validation Errors**: Keep track of common validation failures to improve prompts

## Error Recovery

```python
def safe_validate(
    raw_output: str,
    model_class: Type[T],
    fallback_value: Optional[T] = None
) -> Optional[T]:
    """Validate with graceful fallback"""
    try:
        return validate_output(raw_output, model_class)
    except Exception as e:
        print(f"Validation failed, using fallback: {e}")
        return fallback_value
```
