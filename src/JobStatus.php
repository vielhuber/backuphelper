<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

enum JobStatus
{
    case Created;
    case Skipped;
}
