import requests
import json
import os

def list_free_models():
    url = "https://openrouter.ai/api/v1/models"
    try:
        response = requests.get(url)
        response.raise_for_status()
        models = response.json().get('data', [])
        
        free_models = []
        for model in models:
            # Check if any of the pricing fields are 0
            pricing = model.get('pricing', {})
            # pricing contains 'prompt', 'completion', 'request', 'image'
            # For free models, prompt and completion are usually "0"
            is_free = (
                float(pricing.get('prompt', 1)) == 0 and 
                float(pricing.get('completion', 1)) == 0
            )
            
            if is_free:
                free_models.append({
                    'id': model.get('id'),
                    'name': model.get('name'),
                    'context_length': model.get('context_length')
                })
        
        print(f"Found {len(free_models)} free models:\n")
        print(f"{'ID':<60} | {'Name':<30} | {'Ctx'}")
        print("-" * 105)
        for m in sorted(free_models, key=lambda x: x['id']):
            print(f"{m['id']:<60} | {m['name']:<30} | {m['context_length']}")
            
    except Exception as e:
        print(f"Error fetching models: {e}")

if __name__ == "__main__":
    list_free_models()
