"""Primitive value validation shared by codecs and show validation."""




def text_value(value, label, limit=1024, empty=False):
    if not isinstance(value, str) or '\0' in value or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f'{label} must be text of at most {limit} characters' + ('.' if empty else ' and cannot be blank.'))
    return value


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be an integer from {low} to {high}.')
    return value

