"""Non-blocking batch growth alerts collected for final telemetry only."""
from .batches import individual


def collect(ctx):
    percent = ctx.job.batch_size_alert_percent
    if not percent:
        return []
    alerts = []
    for key, control in ctx.state['active'].items():
        if individual(control) or len(control['files']) == 1:
            continue
        size = sum(f['size'] for f in control['files'])
        if size * 100 >= ctx.job.max_bytes * percent:
            message = f'Job {ctx.job.name}: lote {key} com {size} bytes, alerta em {percent:g}% de {ctx.job.max_bytes} bytes'
            ctx.event('batch_size_alert', archive=key, bytes=size, threshold_percent=percent, message=message,
                      dry_run=ctx.config.dry_run)
            alerts.append(message)
    return alerts
