"""
R2 对象存储模块 — Cloudflare R2 文件上传/删除。

通过 boto3 S3 兼容协议与 R2 交互。
公共接口：
    R2Storage — 封装上传/删除操作
"""

from __future__ import annotations


def _get_s3_client(config):
    """创建并返回 boto3 S3 客户端。

    从 config 对象读取 R2 凭证和 endpoint。
    提取为独立函数便于测试 mock。
    """
    import boto3

    endpoint_url = f"https://{config.r2_account_id}.r2.cloudflarestorage.com"

    return boto3.client(
        service_name="s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=config.r2_access_key_id,
        aws_secret_access_key=config.r2_secret_access_key,
        region_name="auto",
    )


class R2Storage:
    """Cloudflare R2 对象存储客户端。

    封装文件上传和删除操作，上传成功后返回公开访问 URL。
    """

    def __init__(self, config) -> None:
        """初始化 R2 客户端。

        Args:
            config: 包含 r2_* 属性的配置对象（如 config.Settings 实例）。
        """
        self._config = config
        self._s3 = _get_s3_client(config)

    def upload(self, local_path: str, remote_key: str) -> str:
        """上传本地文件到 R2 并返回公开 URL。

        Args:
            local_path: 本地文件路径。
            remote_key: R2 中的对象 key（如 "B0XXX/01_ru.jpg"）。

        Returns:
            拼接后的公开 URL。
        """
        self._s3.upload_file(
            local_path,
            self._config.r2_bucket,
            remote_key,
        )

        domain = self._config.r2_public_domain.rstrip("/")
        return f"{domain}/{remote_key}"

    def delete(self, remote_key: str) -> None:
        """删除 R2 中的文件。

        Args:
            remote_key: R2 中的对象 key。
        """
        self._s3.delete_object(
            Bucket=self._config.r2_bucket,
            Key=remote_key,
        )
