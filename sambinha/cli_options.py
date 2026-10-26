"""Single table of CLI override options: name -> (type, help).

The table builds the shared argparse parent and defines which names reach
config.load, so the command line cannot drift from Config/Job fields.
`type` is str, int, float, bool (--x/--no-x) or a tuple of allowed choices.
"""
from typing import NamedTuple


class Option(NamedTuple):
    type: type | tuple
    help: str


LEVELS = ("error", "summary", "detailed")

OVERRIDES: dict[str, Option] = {
    "job": Option(str, "Executar apenas o job nomeado"),
    "dry_run": Option(bool, "Simular sem alterar origem nem destino"),
    "max_delete_percent": Option(float, "Sobrepor o limite percentual de remoções do job"),
    "max_delete": Option(int, "Sobrepor max_delete absoluto do job simples; requer --job"),
    "fingerprint_cache": Option(bool, "Usar ou ignorar o cache de impressões digitais"),
    "verification_mode": Option(("simple", "hash"),
                                "Comparar tamanho/data de alteração (simple) ou conteúdo (hash)"),
    "log_file_level": Option(LEVELS, "Nível de detalhe do arquivo de log"),
    "log_terminal_level": Option(LEVELS, "Nível de detalhe do terminal"),
    "source": Option(str, "Sobrepor a origem do job"),
    "destination": Option(str, "Sobrepor o destino do job"),
    "type": Option(str, "Sobrepor o tipo do destino (local ou s3)"),
    "logic": Option(str, "Sobrepor a lógica do job (simple ou batch)"),
    "storage_class": Option(str, "Classe de armazenamento dos lotes no S3"),
    "control_storage_class": Option(str, "Classe de armazenamento dos arquivos de controle no S3"),
    "compressor": Option(str, "Compactador dos lotes"),
    "archive_format": Option(str, "Formato dos lotes compactados"),
    "state_dir": Option(str, "Pasta do estado local"),
    "log_dir": Option(str, "Pasta dos logs"),
    "metadata_dir": Option(str, "Pasta das réplicas de metadados"),
    "cronitor_url": Option(str, "URL base do Cronitor para notificações"),
    "rclone": Option(str, "Executável do rclone"),
    "rclone_config": Option(str, "Arquivo de configuração do rclone"),
    "secrets_file": Option(str, "Arquivo de segredos (vazio desativa o carregamento)"),
    "exclude_files": Option(str, "Arquivo de exclusões"),
    "source_mount": Option(str, "Ponto de montagem esperado da origem"),
    "destination_mount": Option(str, "Ponto de montagem esperado do destino"),
    "max_megabytes": Option(float, "Limite de bytes originais por lote em MB (1 MB = 1.048.576 bytes)"),
    "deleted_days": Option(int, "Dias de retenção de arquivos removidos"),
    "version_days": Option(int, "Dias de retenção de versões antigas"),
    "log_retention_days": Option(int, "Dias de retenção dos logs (0 desativa a limpeza)"),
    "compression_level": Option(int, "Nível de compressão dos lotes"),
    "compression_threads": Option(int, "Threads de compressão por lote"),
    "recovery_index_versions": Option(int, "Versões do índice de recuperação mantidas"),
    "timeout": Option(int, "Tempo limite em segundos das operações externas"),
    "deletion_alert_threshold": Option(int, "Remoções que disparam alerta"),
    "deletion_min_files": Option(int, "Mínimo de arquivos para avaliar remoção em massa"),
    "verify_interval_days": Option(int, "Intervalo em dias entre verificações do destino"),
    "fingerprint_full_scan_days": Option(int, "Dias entre varreduras completas do cache de impressões"),
    "transfer_workers": Option(int, "Transferências simultâneas"),
    "batch_workers": Option(int, "Lotes preparados simultaneamente"),
    "individual_batch_percent": Option(float, "Percentual de max_megabytes a partir do qual um arquivo vira lote individual"),
    "batch_size_alert_percent": Option(float, "Percentual de max_megabytes acima do qual um lote gera alerta"),
}
