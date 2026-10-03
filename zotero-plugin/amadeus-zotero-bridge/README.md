# Amadeus Zotero Bridge

Zotero 7/10 user-facing companion bridge.

1. Select a regular Zotero item.
2. Right-click it and choose **发送到 Amadeus Companion**.
3. The plugin sends only `item_key` to `POST http://127.0.0.1:17878/zotero/sync`.
4. Amadeus resolves indexed full text, abstract, DOI, or URL.
5. If local Zotero text is unavailable, the backend reuses the existing Qwen Web Research / Browser paper retrieval path.

The plugin does not capture clipboard text and does not modify Zotero data.

## Prerequisite: enable Zotero local API

In Zotero, open **编辑 → 设置 → 高级** and enable **允许其他应用程序与这台计算机上的 Zotero 通信**.

## Package

```powershell
python tools/package_zotero_plugin.py
```

The XPI is written to `release/amadeus-zotero-bridge.xpi`.

## Install

1. Start Amadeus Companion so `127.0.0.1:17878` is listening.
2. In Zotero, open **工具 → 插件**.
3. Use the gear menu and choose **从文件安装插件…**.
4. Select the packaged `.xpi`.
5. Restart Zotero if prompted.
