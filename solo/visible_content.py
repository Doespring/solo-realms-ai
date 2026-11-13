"""Extract supported visible text shapes; reasoning is never an answer fallback."""
def text(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return ''.join(item.get('text','') for item in value if isinstance(item,dict) and item.get('type') in ('text','output_text') and isinstance(item.get('text'),str))
    return ''

def no_visible(metric,reasoning,refusal):
    if refusal:
        metric['error_code']='provider_refusal';return 'Провайдер отклонил ответ. Автоматического повтора нет.'
    if reasoning:
        metric['error_code']='reasoning_without_answer'
        return 'Модель вернула только reasoning без ответа'+(' и исчерпала лимит.' if metric.get('hit_token_limit') else '.')+' Проверьте поддержку thinking OFF и лимит ответа. Повтор оплачивается отдельно и автоматически не запускается.'
    metric['error_code']='empty_response'
    return 'Провайдер завершил запрос без текста ответа. Состояние игры сохранено; автоматического повтора нет.'
