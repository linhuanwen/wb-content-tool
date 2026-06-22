"""
测试 r2_storage.py — R2Storage 类。

原则：mock boto3，验证调用了正确的 S3 方法并传了正确的参数。
不测试真实网络连接。

注意：因为 boto3 可能未安装，测试通过 mock r2_storage 模块中的
_get_s3_client 函数来避免实际 import boto3。
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# 共同的 FakeConfig，模拟 config.Settings 的 R2 相关属性
class FakeR2Config:
    r2_access_key_id = "test-key"
    r2_secret_access_key = "test-secret"
    r2_account_id = "abc123"
    r2_bucket = "my-bucket"
    r2_public_domain = "https://pub.example.com"


# ============================================================
# 切片1: upload — 上传文件并返回公开 URL
# ============================================================


class TestR2Upload:
    """R2Storage.upload() 上传文件到 R2。"""

    def test_upload_calls_s3_upload_file(self):
        """验证 upload 调用了 boto3 s3.upload_file，参数正确"""
        mock_s3 = MagicMock()

        # Mock r2_storage._get_s3_client 返回 mock s3
        with patch("r2_storage._get_s3_client", return_value=mock_s3):
            from r2_storage import R2Storage

            storage = R2Storage(FakeR2Config())
            result = storage.upload("/tmp/test.jpg", "B0XXX/01_ru.jpg")

            # 验证 s3.upload_file 被正确调用
            mock_s3.upload_file.assert_called_once_with(
                "/tmp/test.jpg",
                "my-bucket",
                "B0XXX/01_ru.jpg",
            )

            # 返回公开 URL
            assert result == "https://pub.example.com/B0XXX/01_ru.jpg"

    def test_upload_handles_public_domain_without_trailing_slash(self):
        """public_domain 无尾部斜杠时也能正确拼接 URL"""
        mock_s3 = MagicMock()

        with patch("r2_storage._get_s3_client", return_value=mock_s3):
            from r2_storage import R2Storage

            config = FakeR2Config()
            config.r2_public_domain = "https://pub.example.com"  # 无尾部斜杠
            storage = R2Storage(config)
            result = storage.upload("local.jpg", "path/to/img.jpg")
            assert result == "https://pub.example.com/path/to/img.jpg"

    def test_upload_handles_public_domain_with_trailing_slash(self):
        """public_domain 有尾部斜杠时也能正确拼接 URL（不双斜杠）"""
        mock_s3 = MagicMock()

        with patch("r2_storage._get_s3_client", return_value=mock_s3):
            from r2_storage import R2Storage

            config = FakeR2Config()
            config.r2_public_domain = "https://pub.example.com/"
            storage = R2Storage(config)
            result = storage.upload("local.jpg", "path/to/img.jpg")
            assert result == "https://pub.example.com/path/to/img.jpg"


# ============================================================
# 切片2: delete — 删除远程文件
# ============================================================


class TestR2Delete:
    """R2Storage.delete() 删除 R2 上的文件。"""

    def test_delete_calls_s3_delete_object(self):
        """验证 delete 调用了 boto3 s3.delete_object，参数正确"""
        mock_s3 = MagicMock()

        with patch("r2_storage._get_s3_client", return_value=mock_s3):
            from r2_storage import R2Storage

            storage = R2Storage(FakeR2Config())
            storage.delete("B0XXX/old_img.jpg")

            mock_s3.delete_object.assert_called_once_with(
                Bucket="my-bucket",
                Key="B0XXX/old_img.jpg",
            )

    def test_delete_uses_correct_bucket(self):
        """验证 delete 使用构造时传入的 bucket 名称"""
        mock_s3 = MagicMock()

        with patch("r2_storage._get_s3_client", return_value=mock_s3):
            from r2_storage import R2Storage

            config = FakeR2Config()
            config.r2_bucket = "custom-bucket-name"
            storage = R2Storage(config)
            storage.delete("some/key.png")

            mock_s3.delete_object.assert_called_once_with(
                Bucket="custom-bucket-name",
                Key="some/key.png",
            )
