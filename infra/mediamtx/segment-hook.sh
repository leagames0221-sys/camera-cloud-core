#!/bin/sh
# Called by MediaMTX (runOnRecordSegmentComplete) after each recording segment is closed.
# The writer of the stamp puts the call on its queue and answers at once. MTX_PATH, MTX_SEGMENT_PATH and MTX_SEGMENT_DURATION are set by MediaMTX (RTSP_PORT and G1, G2 ... too, not used here). The start time is not passed; the receiver
# reads it from the file name. A lost call is made up later by the API's reconciliation, so a failure is only logged.
wget -q -O /dev/null \
  --header "X-Hook-Secret: $(cat /run/secret/hook_secret)" \
  --post-data "path=${MTX_PATH}&file=${MTX_SEGMENT_PATH}&duration=${MTX_SEGMENT_DURATION}" \
  http://writer:8000/internal/v1/segments || echo "segment hook failed for ${MTX_SEGMENT_PATH}" >&2
