<?php
declare(strict_types=1);

namespace vielhuber\backuphelper;

enum SourceType: string
{
    case Path = 'path';
    case Git = 'git';
    case Command = 'command';
    case Ftpsh = 'ftpsh';
}
