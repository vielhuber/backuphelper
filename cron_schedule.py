#!/usr/bin/python3
import re
import sys
import time
from datetime import datetime, timedelta


MONTHS = {name: index for index, name in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(), 1)}
WEEKDAYS = {name: index for index, name in enumerate('sun mon tue wed thu fri sat'.split())}


def expand(field, minimum, maximum, names):
    values = set()
    for part in field.split(','):
        match = re.fullmatch(r'(\*|[0-9]+|[a-z]{3})(?:-([0-9]+|[a-z]{3}))?(?:/([0-9]+))?', part.lower())
        if not match:
            raise ValueError(f'invalid cron field: {field}')
        first, last, step = match.groups()
        if first == '*':
            if last is not None:
                raise ValueError(f'invalid cron range: {field}')
            start, end = minimum, maximum
        else:
            if step is not None and last is None:
                raise ValueError('cron steps require a range or *')
            start = names.get(first, first)
            end = names.get(last, last) if last is not None else start
            start, end = int(start), int(end)
            if maximum == 7 and end == 0 and start > 0:
                end = 7
        step = int(step) if step else 1
        if not minimum <= start <= end <= maximum or step < 1:
            raise ValueError(f'cron field outside its allowed range: {field}')
        values.update(range(start, end + 1, step))
    return values


def parse(expression):
    fields = expression.split()
    if len(fields) != 5 or '\n' in expression or '\r' in expression:
        raise ValueError('schedule needs exactly five cron fields: minute hour day-of-month month day-of-week')
    minute = expand(fields[0], 0, 59, {})
    hour = expand(fields[1], 0, 23, {})
    day = expand(fields[2], 1, 31, {})
    month = expand(fields[3], 1, 12, MONTHS)
    weekday = {value % 7 for value in expand(fields[4], 0, 7, WEEKDAYS)}
    wildcard_day = fields[2].startswith('*') or fields[4].startswith('*')
    return minute, hour, day, month, weekday, wildcard_day


def due(expression, since, now):
    minute, hour, day, month, weekday, wildcard_day = parse(expression)
    if since >= now:
        return False
    date = datetime.fromtimestamp(now).date()
    first_date = datetime.fromtimestamp(since).date()
    while date >= first_date:
        day_matches = date.day in day
        weekday_matches = (date.weekday() + 1) % 7 in weekday
        matches = day_matches and weekday_matches if wildcard_day else day_matches or weekday_matches
        if date.month in month and matches:
            for selected_hour in sorted(hour, reverse=True):
                for selected_minute in sorted(minute, reverse=True):
                    scheduled = datetime(date.year, date.month, date.day, selected_hour, selected_minute)
                    # Round trips exclude missing DST times; both folds of a repeated hour are considered.
                    for daylight in (0, 1):
                        timestamp = time.mktime((*scheduled.timetuple()[:8], daylight))
                        if since < timestamp <= now and datetime.fromtimestamp(timestamp) == scheduled:
                            return True
        date -= timedelta(days=1)
    return False


if __name__ == '__main__':
    try:
        if len(sys.argv) == 2:
            parse(sys.argv[1])
        elif len(sys.argv) == 4:
            sys.exit(0 if due(sys.argv[1], float(sys.argv[2]), float(sys.argv[3])) else 1)
        else:
            raise ValueError('usage: cron_schedule.py "minute hour day-of-month month day-of-week" [since now]')
    except ValueError as error:
        print(f'Invalid cron schedule: {error}', file=sys.stderr)
        sys.exit(2)
