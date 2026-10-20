"""Adapt the standard ComfyUI queue executor to the isolated Colab broker."""
import time


def executor_class(broker):
    class RemoteExecutor:
        def __init__(self, server, **kwargs):
            self.server = server
            self.reset()

        def reset(self):
            self.success = False
            self.status_messages = []
            self.history_result = {"outputs": {}, "meta": {}}

        def execute(self, prompt, prompt_id, extra_data, execute_outputs):
            self.reset()
            sid = extra_data.get("client_id")
            self.server.client_id = sid
            self.server.send_sync("execution_start", {"prompt_id": prompt_id}, sid)
            try:
                result = broker.execute(
                    prompt, prompt_id, extra_data,
                    queue_has_more=lambda: self.server.prompt_queue.get_tasks_remaining() > 1,
                    partial_targets=execute_outputs)
                self.success = bool(result.get("success"))
                self.history_result = {"outputs": result.get("outputs", {}), "meta": result.get("meta", {})}
                self.status_messages = result.get("messages", [])
                if not self.success:
                    raise RuntimeError(result.get("error", "Colab 生成失败；请查看任务详情。"))
                for node, output in self.history_result["outputs"].items():
                    self.server.send_sync("executed", {"node": node, "display_node": node,
                                                       "output": output, "prompt_id": prompt_id}, sid)
                self.server.send_sync("execution_success", {"prompt_id": prompt_id}, sid)
            except Exception as error:
                self.success = False
                data = {"prompt_id": prompt_id, "node_id": "", "node_type": "Colab",
                        "exception_message": str(error), "exception_type": type(error).__name__,
                        "executed": [], "traceback": [], "current_inputs": {}, "current_outputs": [],
                        "timestamp": int(time.time() * 1000)}
                self.status_messages.append(("execution_error", data))
                self.server.send_sync("execution_error", data, sid)

    return RemoteExecutor
